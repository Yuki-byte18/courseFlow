# 002 · 身份认证与权限控制（Auth & RBAC）

| 项目 | 内容 |
|---|---|
| **Status** | `implemented` |
| **作者** | 组长 |
| **负责人** | 组长 |
| **关联里程碑** | M0 |
| **关联目录** | `backend/internal/identity/`、`backend/internal/platform/auth/`、`backend/internal/platform/middleware/` |
| **关联迁移** | `0001_rbac_schema.sql`、`0002_permissions_seed.sql` |
| **创建日期** | 2026-10-27 |

---

## 1. 背景与目标

### 要解决什么问题

系统有**三个角色**（学生 / 教师 / 教务管理员）和**三组路由**（`/student` `/teacher` `/admin`）。
所有后续业务（抢课、排课、名单、大盘）都建立在"知道你是谁、你能干什么"之上。

没有这一层，后面的每个接口都得自己判断角色，权限逻辑会散落到几十个 handler 里，迟早漏。

### 本次目标

- [x] 学生可以自助注册、登录，拿到 JWT 后调用受保护接口
- [x] 教师 / 管理员账号由管理员创建，**注册接口无法创建非学生角色**
- [x] 接口级鉴权：未登录 401、权限不足 403、成功放行
- [x] 权限模型落地为经典 RBAC 五表（用户-角色-权限 + 两张关联表）
- [x] 有首个管理员的引导方案（`cmd/seed`），不依赖手工插库

### 不在本次范围

- ❌ **校园统一身份认证（SSO）** —— 项目非目标，见 `docs/GOALS.md`
- ❌ **教师自助注册 + 审批流** —— 后续可选加分项，当前由管理员直接建号
- ❌ **改密码** —— 已知限制，见 §9
- ❌ **refresh token / token 主动撤销** —— 已知限制，见 §9
- ❌ **登录失败次数限制 / 账号锁定** —— 排到 M2 与限流一起做
- ❌ **禁用 / 启用账号、修改角色** —— 排到 M4 管理端完善时

---

## 2. 用户故事

| 角色 | 我想要 | 以便 |
|---|---|---|
| 学生 | 用学号注册并登录 | 进入选课台抢课 |
| 学生 | 前端知道我有哪些权限 | 只显示我有权访问的菜单和按钮 |
| 教师 | 由教务处给我开号 | 不用自己注册，"教师"这个身份本来就不是自证的 |
| 教师 | 登录后只看到自己的课和名单 | 不会误看到别人的学生信息 |
| 管理员 | 创建教师和管理员账号 | 系统能正常运转，不必手工改数据库 |
| 管理员 | 有全量权限管理排课、轮次、大盘 | 完成教务工作 |

---

## 3. 业务不变量（正确性红线）

| # | 不变量 | 在哪保证 |
|---|---|---|
| **INV-1** | **注册接口只能创建 `student` 角色**，请求体里不得出现角色字段 | `identity/dto.go` 的 `RegisterRequest` 无角色字段；`service.Register` 硬编码 `RoleStudent` |
| **INV-2** | **身份只从 JWT 推导，绝不从请求参数读取** | `middleware.RequireAuth` 把身份写进 `context`；业务层只能通过 `auth.FromContext` 拿 |
| **INV-3** | **未认证 → 401；已认证但权限不足 → 403** | `middleware/auth.go` 的 `abortUnauthorized` / `abortForbidden` |
| **INV-4** | **登录失败文案不得区分「用户不存在」和「密码错误」** | 两种情况都返回 `ErrInvalidCredential` → `"用户名或密码错误"` |
| **INV-5** | **用户不存在时也必须执行一次 bcrypt 比较**（抹平响应时间） | `service.Login` 中 `CompareHashAndPassword(dummyHash, ...)` |
| **INV-6** | **密码只以 bcrypt 哈希存储，任何接口都不得返回** | `users.password_hash`；所有响应 DTO 无此字段 |
| **INV-7** | **JWT 原文不得写入日志** | `middleware.logAuthFailure` 只记失败原因，不记 token |
| **INV-8** | **管理员建号是唯一可指定角色的入口，且必须持 `user:write`** | `controller.RegisterAdmin` 上挂 `RequirePerm(perms.UserWrite)` |
| **INV-9** | **生产环境不得使用内置 jwt.secret** | `config.Validate`：`app.env == prod` 且 secret 为默认值时启动失败 |
| **INV-10** | **用户名全局唯一，并发注册不得产生两条同名记录** | `users` 表 `uk_users_username` 唯一索引兜底 |
| **INV-11** | **JWT 校验必须限定签名算法为 HS256** | `auth.TokenManager.Parse` 的 `jwt.WithValidMethods(...)`，防 `alg=none` 混淆 |

---

## 4. 接口契约

详细定义见同目录 [`openapi.yaml`](openapi.yaml)。

| 方法 | 路径 | 说明 | 需要权限 |
|---|---|---|---|
| POST | `/api/v1/auth/register` | 注册学生账号 | 无 |
| POST | `/api/v1/auth/login` | 登录，签发 token | 无 |
| POST | `/api/v1/auth/logout` | 退出登录（仅审计） | 登录 |
| GET | `/api/v1/me` | 当前用户 + 角色 + 权限 | 登录 |
| POST | `/api/v1/admin/users` | 管理员建号（可指定角色） | 登录 + 角色 `admin` + 权限 `user:write` |

**通用约定**

- **认证方式**：`Authorization: Bearer <jwt>`
- **token 有效期**：2 小时（`jwt.expire` 可配）
- **错误响应结构**：`{"error":{"code":"...","message":"...","request_id":"..."}}`
- **请求 ID 透传**：请求头 `X-Request-ID`（未带则由服务端生成并回写）

---

## 5. 行为描述

### 5.1 注册主流程

```
客户端 POST /auth/register {username, password, real_name, email?}
   │
   ├─ 参数校验失败 → 400 invalid_argument
   ├─ 用户名已存在 → 409 username_taken
   ▼
bcrypt 生成哈希（cost=10）
   │
   ▼
事务：INSERT users → 查 roles.student → INSERT user_roles
   │        （撞唯一索引 → 翻译为 409 username_taken）
   ▼
查该用户的 roles + perms
   │
   ▼
签发 JWT → 201 {token, expires_at, user}
```

注册成功后**直接返回 token**，省掉"注册完再登录一次"的一跳。

### 5.2 登录主流程

```
客户端 POST /auth/login {username, password}
   │
   ▼
按 username 查用户
   ├─ 不存在 → 跑一次 dummy bcrypt → 401 invalid_credential
   ├─ 密码不匹配 → 401 invalid_credential
   ├─ status = 禁用 → 403 user_disabled
   ▼
查 roles + perms（两条 SQL：角色一条、权限一条）
   │
   ▼
签发 JWT（claims 含 uid / username / real_name / roles / perms）
   │
   ▼
200 {token, expires_at, user}
```

### 5.3 权限注入流程

```
请求携带 Authorization: Bearer <jwt>
   │
   ▼
RequireAuth：
   ├─ 无 header → 401 missing_token
   ├─ 非 Bearer 格式 → 401 malformed_token
   ├─ 过期 → 401 token_expired
   ├─ 签名/格式/签发者不合法 → 401 invalid_token
   └─ 通过 → 把 auth.User 塞进 request context
   │
   ▼
RequireRole / RequirePerm：从 context 取用户，检查 roles / perms
   ├─ 上下文无用户 → 401（说明中间件挂载顺序错了）
   ├─ 不满足 → 403 forbidden
   └─ 通过 → 业务 handler
```

### 5.4 错误码表

| code | HTTP | 触发条件 | 前端应如何处理 |
|---|---|---|---|
| `invalid_argument` | 400 | 参数校验失败（字段缺失、长度、邮箱格式、密码强度） | 展示具体字段错误 |
| `invalid_credential` | 401 | 用户名不存在 **或** 密码错误 | 提示"用户名或密码错误"，不区分 |
| `token_expired` | 401 | token 已过期 | 跳转登录页 |
| `invalid_token` | 401 | 签名不对、被篡改、签发者不符 | 清 token，跳转登录页 |
| `missing_token` | 401 | 没有 Authorization 头 / 格式不是 Bearer | 跳转登录页 |
| `malformed_token` | 401 | `Authorization` 不是 `Bearer xxx` 形式 | 跳转登录页 |
| `user_disabled` | 403 | 账号被禁用 | 提示联系管理员 |
| `forbidden` | 403 | 已登录但角色/权限不足 | 提示无权访问，回到有权限的页面 |
| `username_taken` | 409 | 用户名已被占用 | 提示换一个用户名 |
| `role_missing` | 500 | `roles` 表未初始化 | 提示联系管理员（运维问题） |
| `internal_error` | 500 | 未预期错误 | 通用错误提示 |

### 5.5 边界条件

| 场景 | 期望行为 |
|---|---|
| 并发注册同一用户名 | 预检查可能同时通过，最终靠唯一索引；后到的返回 409 |
| token 在请求处理中途过期 | 以请求进入时的校验结果为准，本次请求正常处理 |
| 用户已登录但被管理员禁用 | 旧 token 在有效期内仍可用（JWT 无状态），最多 2 小时后失效 |
| 角色权限在运行期被修改 | 旧 token 里的 `perms` 不变，需重新登录才生效 |
| `admin` 用户名已被学生注册后运行 seed | **seed 报错退出，不自动提权** |
| 重复运行 seed | 幂等，打印"已存在，跳过"，退出码 0 |
| 请求体里带 `role` 字段 | 被 JSON 绑定忽略（结构体无该字段），注册结果仍是 student |

---

## 6. 非功能要求

| 维度 | 要求 | 如何验证 |
|---|---|---|
| **登录延迟** | p99 < 300ms（bcrypt cost=10 本身就占 50~80ms） | 压测 / 单次计时 |
| **鉴权延迟** | 中间件本地校验，p99 < 1ms（不查库） | 压测 |
| **token 并发安全** | 无状态，天然支持多实例 | 设计保证 |
| **密钥强度** | `jwt.secret` ≥ 16 字符；生产禁用默认值 | `config.Validate` |
| **密码强度** | 自助注册 ≥ 8；管理员建号 ≥ 6（初始密码，首次登录应修改） | validator `min=8` / `min=6` |
| **可观测** | 登录/注册/退出/建号均有结构化审计日志；认证失败记录原因（不含 token） | 日志检查 |
| **数据库约束** | 五张表均有外键；`role_permissions` / `user_roles` 复合主键防重复授权 | DDL 检查 |

---

## 7. 验收标准

### AC-1 学生自助注册

- **Given** 一个未注册的用户名
- **When** `POST /auth/register` 提交合法参数
- **Then** 返回 **201**，响应含 `token`、`expires_at`、`user`，且 `user.roles == ["student"]`
- **验证方式**：手工 curl + 集成测试

### AC-2 注册无法提权

- **Given** 请求体里额外带上 `"roles": ["admin"]`
- **When** `POST /auth/register`
- **Then** 注册出的账号**仍然只有 student 角色**（多余字段被忽略）
- **验证方式**：集成测试

### AC-3 用户名重复

- **Given** 用户名已存在
- **When** 再次注册
- **Then** 返回 **409**，`error.code == "username_taken"`
- **验证方式**：手工 curl

### AC-4 密码过短

- **Given** 密码只有 3 位
- **When** `POST /auth/register`
- **Then** 返回 **400**，`error.code == "invalid_argument"`
- **验证方式**：手工 curl

### AC-5 登录成功

- **Given** 已注册且状态正常的账号
- **When** `POST /auth/login` 提交正确密码
- **Then** 返回 **200**，token 可用，`user.perms` 是数据库里该角色真实拥有的权限码（学生 6 个）
- **验证方式**：手工 curl + 集成测试

### AC-6 登录失败不泄露账号存在性

- **Given** 一个不存在的用户名和一个存在但密码错误的账号
- **When** 分别调用登录
- **Then** 两者返回**相同的 401 + `invalid_credential` + 相同文案**，且响应耗时差异不明显
- **验证方式**：手工 curl + 计时对比

### AC-7 未登录访问受保护接口

- **Given** 不带 `Authorization` 头
- **When** `GET /me`
- **Then** 返回 **401**，`error.code == "missing_token"`
- **验证方式**：手工 curl + 单测（已覆盖）

### AC-8 token 过期

- **Given** 一个过期 token
- **When** 访问受保护接口
- **Then** 返回 **401**，`error.code == "token_expired"`
- **验证方式**：单测（`IssueWithTTL` 造负有效期，已覆盖）

### AC-9 token 被篡改

- **Given** token 末尾追加任意字符
- **When** 访问受保护接口
- **Then** 返回 **401**，`error.code == "invalid_token"`
- **验证方式**：单测（已覆盖）

### AC-10 权限不足

- **Given** 教师账号的 token（只有 3 个权限，不含 `user:write`）
- **When** `POST /admin/users`
- **Then** 返回 **403**，`error.code == "forbidden"`
- **验证方式**：手工 curl —— **这是证明 RBAC 真正生效的关键一条**

### AC-11 管理员建号

- **Given** 管理员 token（含 `user:write`）
- **When** `POST /admin/users` 指定 `roles: ["teacher"]`
- **Then** 返回 **201**，新账号可登录且 `roles == ["teacher"]`
- **验证方式**：手工 curl

### AC-12 seed 幂等

- **Given** 已存在管理员 `admin`
- **When** 再次执行 `go run ./cmd/seed`
- **Then** 打印"已存在，无需重复创建"，退出码 0，**不创建重复记录**
- **验证方式**：手工执行两次

### AC-13 seed 拒绝提权

- **Given** 用户名 `admin` 已被一个学生账号占用
- **When** 执行 seed
- **Then** **报错退出（非 0）**，不自动给该账号加 admin 角色
- **验证方式**：手工构造场景

### AC-14 生产环境密钥校验

- **Given** `app.env = prod` 且 `jwt.secret` 仍是内置默认值
- **When** 启动服务
- **Then** 启动失败并给出明确错误
- **验证方式**：手工改配置启动

### AC-15 错误响应结构统一

- **Given** 任意一个失败请求
- **When** 返回错误
- **Then** 响应体形如 `{"error":{"code","message","request_id"}}`，三个字段齐全
- **验证方式**：单测（已覆盖）

---

## 8. 实现要点（关键取舍）

| 取舍点 | 选择 | 理由 | 代价 |
|---|---|---|---|
| **权限放在 JWT 还是每次查库** | 放 JWT | 抢课是毫秒级热路径，每请求多一次 DB/Redis 查询就是实打实的延迟 | 权限变更需等 token 过期（最长 2h）才生效 |
| **退出登录怎么实现** | 只记审计 + 客户端丢弃 token | JWT 无状态，服务端本来就没有"会话"可销毁 | token 在有效期内仍可用 |
| **教师账号从哪来** | 管理员创建 | "教师"是外部权威授予的身份，自证没有意义；自助注册教师会把 PII 泄露的防线全押在"数据过滤一处都不能错"上 | 教师无法自助开通 |
| **首个管理员从哪来** | `cmd/seed` 从环境变量创建 | 可复现、不泄露密码、能写进部署文档 | 多一个二进制 |
| **注册是否返回 token** | 返回 | 省掉前端一次登录跳转 | 注册接口也要用签发逻辑 |
| **密码哈希算法** | bcrypt cost=10 | 自带 salt、业界标准、Go 生态原生支持 | 每次登录约 50–80ms |
| **用户不存在时的处理** | 跑一次 dummy bcrypt | 抹平响应时间，防止账号枚举 | 每次失败登录多花一次哈希时间 |
| **来源唯一原则** | 身份只在 `RequireAuth` 里从 token 推导 | 集中一处，业务层不存在"从参数读 user_id"的可能 | 每一处取身份都要走 context |
| **服务端不 import 业务域** | `platform/server` 只暴露路由组，由 `main` 挂载 | 守住"platform 被依赖但不依赖别人"的分层契约 | 路由装配集中在 main |

---

## 9. 已知限制

| 限制 | 影响 | 何时解除 |
|---|---|---|
| 没有改密码接口 | seed 的 `123456` 和教师初始密码无法自助修改 | M5 之后 / 按需 |
| 没有 refresh token | 2 小时到期后用户需重新登录 | 不做，生产可改为短 access + refresh |
| 退出登录不使 token 失效 | token 泄露后有效期内仍可用 | 如需解除：Redis 存 jti 黑名单，TTL = 剩余有效期 |
| 权限变更需重新登录 | 管理员调整权限后，用户最多 2 小时后才生效 | 如需即时：JWT 只放 uid，角色从 Redis 缓存读 |
| 没有登录失败锁定 | 密码可被无限次尝试（受 bcrypt 速度天然限流） | M2 与限流一起做 |
| **学生注册完全开放** | 任何人注册即可抢课 | 真实系统应有学号白名单 / 邀请码 / SSO。本项目非目标明确不对接 SSO，接受此风险 |
| 没有 identity 域单测 | 注册/登录的业务分支目前只有手工验证 | 本 PR 或紧接着补 |

---

## 10. 变更记录

| 日期 | 变更 | 人 |
|---|---|---|
| 2026-10-27 | 初稿：先实现后补文档（违反 SDD 顺序，已在 PR 描述中说明） | 组长 |
