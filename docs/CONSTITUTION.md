# 项目总宪法（CONSTITUTION）

> **这份文档是给"写代码的东西"看的。** 不管是 Cursor、Codex、Claude Code、DSH 还是人，
> 动手之前先读这里。
>
> 它只回答三件事：**不能违反什么、代码往哪放、怎么交付。**
> 详细的"为什么"在 `ARCHITECTURE.md` 和 `TECH_STACK.md` 里，这里不重复。

---

## 1. 项目是什么（一句话）

courseFlow 是一个**高校选课 / 抢课系统**。用这个真实业务跑通三件事：

1. **高并发读写**——开抢瞬间几千人抢几十个名额，不超卖、不雪崩
2. **在线 / 近线 / 离线三层数据架构**——毫秒级、秒级、T+1，各干各的活
3. **AI Agent 接入业务**——学生用大白话提需求，Agent 查数据、组织结果、给可点击的卡片

**它不是玩具项目。** 判据很简单：每一个技术点都必须能回答三个问题——

> **为什么用它？有什么数据？代价是什么？**

三个都答不上来的技术，不许引入。

---

## 2. 十条铁律

违反任何一条，这个改动就不合规，PR 直接打回。

| # | 铁律 | 怎么自查 |
|---|---|---|
| **1** | **身份只从认证凭据（JWT）推导，绝不从请求参数读取** | 请求体里出现 `student_id` / `teacher_id` 就是错 |
| **2** | **名额不可为负**（0 超卖是红线，不可协商） | 压测后对账：`Redis 库存 + 已选人数 == 容量` |
| **3** | **同一学生同一班次只能有一条记录** | 唯一索引 `uk_student_offering` + 幂等键双保险 |
| **4** | **Agent 的工具集 100% 只读**；写操作由前端持一次性确认令牌发起 | 检查 `internal/agent/tools.go` 里有没有写接口 |
| **5** | **controller 不得直接调 dao**，必须经过 service | `controller → service → dao`，方向不能跳 |
| **6** | **抢课热路径不出现单条 GORM 写入**；批量落库手写 SQL | 检查 `internal/nearline/writer.go` |
| **7** | **禁止 GORM AutoMigrate**；所有 DDL 走 `backend/migrations/*.sql` | 全局搜 `AutoMigrate(` |
| **8** | **禁止提交任何密钥**；`.env` 必须在 `.gitignore` 里 | `git status` 逐行看，`.env` 出现即停 |
| **9** | **一个 PR 只做一件事，改动 ≤ 400 行** | 超过就拆 |
| **10** | **没有 spec 不写对外接口** | 每个 API 必须能对应 `specs/NNN-xxx/openapi.yaml` |

---

## 3. 代码往哪放（速查）

完整目录表见 [`PROJECT_LAYOUT.md`](PROJECT_LAYOUT.md)。这里只给最容易放错的几个：

| 你写的东西 | 放哪 |
|---|---|
| 一段业务规则（先修校验、学分上限、时间冲突） | `backend/internal/rules/` |
| 一个 HTTP 接口 | 对应域的 `controller.go`，不是单独一个 `handler/` 目录 |
| 一条 SQL 查询 | 对应域的 `dao.go` |
| Redis 库存扣减的 Lua 脚本 | `backend/internal/enrollment/stock.go` |
| Kafka 事件结构 | `backend/internal/events/`（**不是** `enrollment/`，因为近线层也要用） |
| 离线数仓的分层 SQL | `warehouse/spark/etl/{ods,dwd,dws,ads}/` |
| 实时窗口聚合的 Flink SQL | `warehouse/flink/sql/` |
| 跨域复用的基础设施 | `backend/internal/platform/` |

**如果都不匹配——停下来问，不要新建 `utils/`、`common/`、`misc/` 这种目录。** 那是混乱的起点。

---

## 4. 分层与依赖方向（不许反向依赖）

每个业务域内部是四层，文件级分层：

```
controller.go    ← 只解析请求、调 service、组装响应
service.go       ← 业务逻辑、事务编排（唯一能跨多个 dao 组合的地方）
dao.go           ← 只做数据库读写，不含业务判断
model.go         ← 数据库实体，一一对应表结构
vo.go            ← 响应结构（不能直接把 model 序列化出去）
dto.go           ← 请求结构
```

依赖方向：

```
controller → service → dao → model
                ↓
          cache / mq / rules / errs
```

**硬约束**：

- `dao` 里不许写业务判断（"名额够不够"不能写进 SQL 层）
- `service` 里不许拼 HTTP 状态码或 JSON
- `model` 不许 import 任何 internal 包（它是最底层）
- 业务域之间只通过**接口（interface）**依赖，实现在 `cmd/*` 装配

---

## 5. 代码风格（Go）

| 项 | 要求 |
|---|---|
| 命名 | 包名小写单词，不用下划线、不用复数；文件名用领域名，不加下划线 |
| 错误 | 用 `errors.Is/As` 判断；业务错误统一走 `platform/errs` 的错误码 |
| Context | 所有跨进程调用（DB / Redis / Kafka / HTTP）必须传 `ctx` 并设超时 |
| 日志 | 用 `zap`，只打结构化字段，**不打拼接字符串** |
| 注释 | 解释「为什么」，不解释「是什么」。导出的函数必须有注释 |
| 复杂度 | 一个函数超过 60 行，先想想能不能拆 |
| 魔法数字 | 不许出现。常量要有名字 |

---

## 6. 提交与协作

- 分支命名、提交信息格式、PR 要求：见 [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **不许直接 push `main`**（规则集已强制，会报 `GH006`）
- 一个 PR 关联一份 spec；没有 spec 的 PR 说明没规划过

---

## 7. 文档打架时以谁为准

按优先级从高到低：

1. **本文件（CONSTITUTION）**——铁律，不可违反
2. **`ARCHITECTURE.md`**——架构边界、进程划分、数据流
3. **`specs/NNN-xxx/`**——具体功能的需求与接口契约
4. **`TECH_STACK.md`**——技术选型
5. **`PROJECT_LAYOUT.md`**——目录归属
6. **`GOALS.md`**——为什么做这个项目

如果发现两份文档矛盾：**以优先级高的为准，同时提 PR 修正另一份。** 不要自己选一个默默执行。

---

## 8. 改动前的三项自检

1. **这个功能有 spec 吗？** 没有就先写 spec（`specs/_template/spec.md`）。
2. **我动的是不是共享目录？** `platform/`、`go.mod`、`frontend/package.json`、`deploy/` 改动前在群里说一句。
3. **会不会碰到十条铁律？** 特别是 1、2、4、8 这四条。

三项都过了再写代码。
