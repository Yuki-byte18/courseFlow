# 协作开发规范（CONTRIBUTING）

> 三个人、15 周。没有规矩会互相踩脚，规矩太多会拖慢进度。
> 这份文档只保留**真正救过命**的那几条。

---

## 一、研发范式：先写规格，再写代码（SDD）

**SDD = Spec-Driven Development，规格驱动开发。** 工作流叫 **spec coding**。

### 为什么这么做

三个人并行时，最贵的事故不是"代码写错了"，而是**"我以为你要的是 A，你要的是 B"**。

规格把分歧提前到**写代码之前**解决。那时改的成本是几行 markdown；代码写完再改是一个 PR。

### 一个功能的完整生命周期

```
① 写规格
   cp -r specs/_template specs/NNN-功能名
   填 spec.md（需求、规则、边界、验收标准）
   有接口就填 openapi.yaml
   Status: draft → review

② 提 PR 评审规格
   这个 PR【只改 specs/】，不写任何实现代码
   在群里 @ 组长和相关模块负责人

③ 评审通过
   Status 改成 approved

④ 切实现分支，按规格写代码 + 写测试
   测试用例直接来自 spec.md 的「验收标准」小节

⑤ 提实现 PR
   描述里【必须】关联 spec 编号
   review 时逐条对照验收标准

⑥ 合并
   Status 改成 implemented
```

### 什么必须写规格

| 情况 | 必须写 spec |
|---|---|
| 新增/修改一个 HTTP 接口 | ✅ 含 openapi |
| 新增表或字段 | ✅ |
| 引入新的 Redis key / Kafka topic | ✅ |
| 一条新的业务规则（学分上限、先修、冲突判定） | ✅ |
| 纯重构（不改对外行为） | ❌ 但 PR 要说明"行为不变" |
| 修 bug | ❌ 但 PR 要写清复现路径 |

**一句话判据：这个东西做错了，别人的代码会不会受影响？** 会 → 写规格。

---

## 二、分支模型

只有一条长期分支：`main`。

```
main  ●────●────●────●────●  ← 永远可编译、可运行、受保护
       \        \        \
       feat/a   feat/b   fix/c     ← 短生命周期，合并后自动删除
```

**规则**

1. `main` **不允许直接 push**（规则集强制，会报 `GH006`）
2. 一切改动走功能分支 + PR
3. 功能分支**从最新 `main` 切出**
4. 一个分支只做一件事，**生命周期 ≤ 3 天**

```bash
git switch main && git pull && git switch -c feat/enrollment-lua-deduct
```

---

## 三、分支命名

格式：`<类型>/<简短英文描述>`，全小写、连字符分隔。

| 类型 | 用于 | 例子 |
|---|---|---|
| `feat/` | 新功能 | `feat/enrollment-lua-deduct` |
| `fix/` | 修 bug | `fix/oversell-on-retry` |
| `docs/` | 只改文档 | `docs/benchmark-methodology` |
| `spec/` | 只改 `specs/` | `spec/nearline-aggregation` |
| `refactor/` | 重构 | `refactor/service-error-handling` |
| `test/` | 补测试 | `test/enrollment-idempotency` |
| `chore/` | 依赖、构建、配置 | `chore/bump-go-version` |

---

## 四、提交信息

采用 **Conventional Commits**：

```
<类型>(<可选范围>): <描述>

<可选正文：为什么这么改，而不是改了什么>

<可选脚注：关联 spec>
```

### 好例子

```
feat(cache): 用 Lua 脚本实现库存原子扣减

把「读库存 → 判断 → 扣减」三步合并到一个 Lua 脚本里，
避免并发下的检查-执行竞态。脚本在 Redis 内单线程执行，天然互斥。

Spec: specs/002-enrollment-core/spec.md
```

```
fix(mq): 消费者重启后重复落库

原因：幂等键只用了 student_id，退课后重新选课会命中旧记录。
改为 (student_id, offering_id, request_id) 三元组。
```

### 类型对照

| 类型 | 含义 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 只改文档 |
| `spec` | 只改规格 |
| `refactor` | 重构（不改对外行为） |
| `test` | 只改测试 |
| `perf` | 性能优化 |
| `chore` | 构建、依赖、CI 等杂项 |

### 硬要求

- 标题 ≤ 72 字符，说清做了什么
- **一个提交只做一件事**。出现"顺便"、"另外"→ 该拆
- **不许**提交 `wip`、`临时`、`aaa`、`测试一下`——Git 历史是课程评分证据的一部分

---

## 五、PR 规范

### 提交前自检

```bash
git switch main && git pull
git switch feat/xxx
git rebase main                  # 把分支搬到最新 main 上
git push --force-with-lease      # 只在【自己的功能分支】上这么做
```

- [ ] `cd backend && go build ./...` 通过
- [ ] `go test -race ./...` 通过
- [ ] `git status` 干净，没有 `.env` / `node_modules` / `dist/` / 密钥
- [ ] 关联了 spec 编号

### PR 大小

| 指标 | 要求 |
|---|---|
| 单个 PR 改动 | **≤ 400 行**（不含自动生成代码） |
| 分支存活 | **≤ 3 天** |
| 提 PR 到合并 | **≤ 2 天** |

> 一个 800 行的 PR，reviewer 只会点"Approve"。拆成两个 400 行，能真的被审出问题。
> **PR 大不是勤奋的证明，是协作的阻碍。**

### 合并方式

**只用 Squash and merge**。一个 PR 压缩成一个提交进 `main`，历史线性，
回滚一个功能 = 回滚一个提交。答辩演示 `git log` 时也好看。

---

## 六、Code Review

### Reviewer

- **48 小时内响应**。看不完就留一句"今天看不了"，别让人干等
- 区分**必须改**和**建议**：必须改的说"必须"，建议的说"可选"
- 指出问题要说**为什么**，别只说"这样不好"
- 认可做得好的地方 —— 一句"这个幂等键设计很稳"能显著提高协作意愿

### 作者

- 收到意见先**理解**再改，不懂就问
- 不同意就说理由，review 的目的是对齐，不是服从
- 改完回复"已改，在 xxx 行"

### 唯一的例外

紧急情况（答辩前一天修 typo）可以在群里说明后用 bypass 放行。
**正常情况组长也必须走 PR**——组长的 PR 由成员 approve。

---

## 七、目录所有权

**目录即边界。** 详细分工见 [`PROJECT_LAYOUT.md`](PROJECT_LAYOUT.md) 最后一节。

### 共享目录（改动前必须先打招呼）

| 目录 / 文件 | 说明 |
|---|---|
| `backend/internal/platform/` | 配置、日志、MySQL、Redis、Kafka 封装 |
| `backend/go.mod`、`frontend/package.json` | 加依赖会影响所有人 |
| `deploy/` | 环境编排 |
| `docs/`、`specs/` | 组长维护 / 各自写自己模块的 |
| `warehouse/ddl/` | 数仓表结构，改了两边都受影响 |

**流程**：在群里说一句"我要改 platform/config 加 llm 段，10 分钟"，比事后解冲突便宜得多。

### 三条冲突规避规则

1. **动手前先 `git pull`**（说三遍都不为过）
2. **PR 要小**（400 行以内的 PR 几乎不会冲突）
3. **按目录切分**（你改 `enrollment/`，别人改 `course/`，物理上不可能冲突）

---

## 八、禁止事项

- ❌ 直接 push `main`
- ❌ `git push --force` 到 `main` 或别人的分支（自己分支用 `--force-with-lease`）
- ❌ 提交 `.env`、任何密钥、`node_modules/`、`dist/`、编译产物
- ❌ 提交 `wip` / 临时 / 无意义信息的提交
- ❌ 把别人的工作以自己的身份提交
- ❌ 没有规格就实现对外接口
- ❌ 在 `dao` 里写业务判断，或在 `controller` 里直接调 `dao`
- ❌ 用 `git reset --hard` / `git clean -fd` 处理别人的工作区

---

## 九、常见问题怎么办

| 症状 | 处理 |
|---|---|
| `push` 被拒：`GH006 Protected branch` | 正常，规则集生效了。切分支提 PR |
| `push` 被拒：`non-fast-forward` | 先 `git pull --rebase`，别 `--force` |
| rebase 冲突 | 改完冲突 → `git add` → `git rebase --continue`；想放弃 `git rebase --abort` |
| 不小心提交了 `.env` | **立刻去服务商后台撤销密钥**，然后告诉组长。改历史不够，密钥已被看到 |
| PR 冲突了 | 本地 rebase main 后 `push --force-with-lease` |
| 忘了切分支，在 main 上写了代码 | `git stash` → 切分支 → `git stash pop` |

---

## 附录：仓库初始化与规则集配置（组长一次性操作）

### A.1 推送前的安全准备（**顺序不能颠倒**）

```powershell
git status     # 逐行确认：没有 .env、没有 node_modules、没有 dist
```

- [ ] `.gitignore` 已就位（含 `.env`、`.env.*`、`!.env.example`）
- [ ] `.env.example` 已就位（模板入库，真实值不入库）
- [ ] 没有硬编码密钥：搜一下 `sk-`、`password=` 之类

### A.2 首次推送

```powershell
git init -b main
git add -A
git status                      # ⚠️ 再看一次
git commit -m "chore: 初始化 courseFlow 项目骨架 (M0)"
```

**在 GitHub 建仓库时什么都不要勾选**（不要 README / .gitignore / license）——
否则远程会多一个本地没有的提交，首次推送被迫 rebase，新手容易在这里丢东西。

```powershell
git remote add origin https://github.com/<用户名>/courseFlow.git
git branch -M main
git push -u origin main
```

### A.3 邀请协作者

`Settings` → `Collaborators` → `Add people` → 输入队友 GitHub 用户名。
**队友必须点邮件里的接受邀请**，否则推送会被拒（403）。

顺带开启：`Settings` → `General` → ✅ `Automatically delete head branches`；
合并方式只保留 **Allow squash merging**。

### A.4 配置 main 规则集

`Settings` → `Rules` → `Rulesets` → `New branch ruleset`

| 项 | 填什么 |
|---|---|
| Ruleset Name | `protect-main` |
| **Enforcement status** | **`Active`** ⚠️ 默认是 Disabled，不改则配了也不生效 |
| Bypass list | 加入**组长本人**（应急通道） |
| Target branches | `Include by pattern` → `main` |

规则逐项：

| 规则 | 开 |
|---|---|
| Restrict deletions | ✅ |
| Block force pushes | ✅ |
| Require a pull request before merging | ✅ |
| └ Required approvals | **1** |
| └ Dismiss stale approvals on new commits | ✅ |
| Require conversation resolution | ✅ |
| Require status checks | ⬜（M3 有 CI 后再开） |
| Require signed commits | ⬜ |

### A.5 验证规则生效（**别跳过**）

```powershell
git commit --allow-empty -m "test: 验证 main 受保护"
git push
```

期望被拒：

```
remote: error: GH006: Protected branch update failed for refs/heads/main.
remote: - Changes must be made through a pull request.
```

撤销测试提交：`git reset --hard HEAD~1`

> 组长自己在 Bypass list 里可能会被放行——那就用队友账号验证。
> **没验证过的保护等于没有保护。**
