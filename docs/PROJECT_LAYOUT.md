# 项目文件夹分层（PROJECT_LAYOUT）

> 这份文档回答两个问题：**每个文件夹属于架构的哪一块？我要写的代码应该放哪？**
>
> 如果某个文件放哪都说不通 —— **停下来问，不要新建 `utils/`、`common/`、`misc/`。**
> 那是混乱的起点。

---

## 一、一张总树

```
courseFlow/
│
├── .github/
│   ├── pull_request_template.md      PR 模板
│   └── workflows/                     CI（M3 起：build / vet / test -race / lint）
│
├── specs/                            📐 规格：先写这里，再写代码
│   ├── README.md                     规格工作流与索引
│   ├── _template/                    spec.md + openapi.yaml 模板
│   └── NNN-功能名/                    001-domain-model、002-enrollment-core …
│
├── docs/                             工程文档（"为什么"）
│   ├── CONSTITUTION.md               ⭐ 项目总宪法（写代码前必读）
│   ├── GOALS.md                      项目目标
│   ├── ARCHITECTURE.md               整体架构
│   ├── TECH_STACK.md                 技术栈
│   ├── CONTRIBUTING.md               协作开发规范
│   ├── PROJECT_LAYOUT.md             本文档
│   ├── notes/                        逐课学习笔记（答辩素材）
│   └── defense/                      答辩材料（PPT、演示脚本）
│
├── backend/                          Go 后端（模块名 courseflow）
│   ├── go.mod
│   ├── configs/                      配置示例（config.yaml 不入库）
│   ├── migrations/                   ⭐ 所有 DDL 的唯一来源
│   ├── cmd/                          可执行程序，每个目录 = 一个二进制
│   │   ├── api/                      在线层 HTTP
│   │   ├── consumer/                 近线层：批量落库 + 对账
│   │   ├── agent/                    业务 Agent 服务
│   │   └── migrate/                  跑数据库迁移
│   ├── internal/                     业务代码（编译器强制：仅本模块可 import）
│   │   ├── platform/                 跨域基础设施
│   │   ├── events/                  领域事件定义
│   │   ├── student/  course/  rules/  enrollment/    业务域
│   │   ├── nearline/                近线层的 Go 部分
│   │   ├── llm/  agent/             Agent 相关
│   └── test/
│       ├── integration/              跨域集成测试
│       └── concurrency/              并发正确性测试（0 超卖、幂等重放）
│
├── warehouse/                        ⭐ 数据开发（Spark / Flink / 数仓 DDL）
│   ├── spark/
│   │   ├── etl/{ods,dwd,dws,ads}/    离线四层 SQL
│   │   └── quality/                  数据质量校验 SQL
│   ├── flink/sql/                    实时窗口聚合
│   └── ddl/                          数仓表结构（Parquet 目录约定 + ClickHouse 表）
│
├── frontend/                         React 前端（一套应用，三组路由）
│   └── src/
│       ├── api/                      接口封装 + openapi 生成的类型
│       ├── pages/                    路由页面
│       ├── components/               可复用展示组件
│       ├── hooks/                    自定义 hooks（useSSE 等）
│       └── styles/
│
├── deploy/                           基础设施编排
│   ├── docker-compose.yml            MySQL / Redis / Kafka / ClickHouse（profiles 按需）
│   ├── nginx/                        生产反代 + 静态托管
│   ├── prometheus/  grafana/         M3
│
├── benchmark/                        压测
│   ├── k6/                           场景脚本（洪峰/稳态/热点/混合/故障注入）
│   ├── scripts/                      造数据 + 压测后对账
│   ├── reports/                      M3 压测报告
│   └── results/                      原始输出（不入库，只提交 .gitkeep）
│
├── .env.example                      环境变量模板
├── .gitignore
├── Makefile                          常用入口（M0 建）
└── README.md                         项目门面（M7 完善）
```

---

## 二、顶层目录 → 属于架构哪一块

| 目录 | 属于 | 谁负责 | 放什么 |
|---|---|---|---|
| `backend/` | 后端（在线层 + 近线层 Go 部分） | 组长 + 成员 A | 所有 Go 代码 |
| `warehouse/` | 后端（近线 Flink + 离线 Spark） | 成员 B | SQL、PySpark、Flink 作业 |
| `frontend/` | 前端 | 成员 B | 三组路由的 React 应用 |
| `deploy/` | 基础设施 | 成员 A | compose、Nginx、监控配置 |
| `benchmark/` | 压测（横跨三层） | 组长 | k6 脚本 + 对账脚本 + 报告 |
| `specs/` | 架构无关（规格先行） | 各自写自己模块的 | 需求、接口契约、验收标准 |
| `docs/` | 架构无关（工程文档） | 组长统筹 | 为什么这样做 |

---

## 三、`backend/internal/` 逐个包说清

### 3.1 `platform/` —— 跨域基础设施

**契约：只依赖第三方库，不依赖任何业务域。所有业务域都可以依赖它。**

| 包 | 职责 |
|---|---|
| `config/` | Viper 配置加载、默认值、校验 |
| `logger/` | zap 构建（含采样） |
| `errs/` | 业务错误码（`ErrSeatSoldOut`、`ErrDuplicateEnroll`…） |
| `middleware/` | 请求 ID、访问日志、恢复、鉴权、限流 |
| `server/` | gin 装配、路由挂载、优雅退出 |
| `metrics/` | Prometheus 指标注册 |
| `mysql/` | 连接池、事务辅助、GORM 日志适配 |
| `redis/` | Redis 客户端 |
| `kafka/` | sarama 生产/消费封装（重试、死信） |

### 3.2 `events/` —— 领域事件定义

**在线层写、近线层读，是两者唯一的耦合点。**

放 `EnrollmentRequested` / `Succeeded` / `Failed` / `Dropped` 这些事件结构 + 编解码。

**为什么不放 `enrollment/` 里**：一旦放进去，`nearline/` 和 Flink 就得依赖整个 enrollment 域，
近线层就被在线层绑死了。

### 3.3 业务域：`student/` `course/` `rules/` `enrollment/`

每个域内部**按文件分层**（不是按目录）：

| 文件 | 职责 | 对应传统叫法 |
|---|---|---|
| `model.go` | 数据库实体，一一对应表结构 | PO / DO / Entity |
| `vo.go` | 响应结构（**不能直接把 model 序列化出去**） | VO |
| `dto.go` | 请求结构 | DTO / Query |
| `dao.go` | 只做数据库读写，**不含业务判断** | DAO / Mapper |
| `service.go` | 业务逻辑、事务编排 | Service |
| `controller.go` | HTTP：解析请求、调 service、组装响应 | Controller |

**不是每个域都需要全部 6 个文件**：

| 域 | 有什么 | 说明 |
|---|---|---|
| `student/` | 全部 6 个 | 身份、课表、学分进度 |
| `course/` | 全部 6 个 | 课程、班次、时间地点、组合筛选 |
| `rules/` | `rules.go` + `ports.go` | **没有 dao / controller**，纯校验逻辑 |
| `enrollment/` | 全部 6 个 + `stock.go` + `idempotency.go` | 抢课核心：Lua 扣减 + 一人一课去重 |

### 3.4 `nearline/` —— 近线层的 Go 部分

只有两件事：

- `writer.go` —— 消费事件，**手写批量 SQL** 落业务库（幂等 + 重试 + 死信）
- `reconcile.go` —— 定时对账，核对"Redis 库存 + 已选人数 == 容量"

**注意**：它**不 import 任何业务域**。因为落库用的是显式批量 SQL，不需要 `enrollment/model`。
这样它只依赖 `events` + `platform`。

**窗口聚合不在这里**，在 `warehouse/flink/sql/`。

### 3.5 `llm/` 和 `agent/`

| 包 | 职责 |
|---|---|
| `llm/` | 供应商抽象 + DeepSeek 实现（`provider.go`、`deepseek.go`） |
| `agent/` | 编排 `service.go`、工具 `tools.go`（**只读**）、护栏 `guard.go`、确认令牌 `confirm.go`、评测 `eval.go` |

### 3.6 `backend/test/`

| 目录 | 放什么 |
|---|---|
| `integration/` | 跨域集成测试，需要真实 MySQL/Redis，用 `//go:build integration` 标记 |
| `concurrency/` | 并发正确性：0 超卖验证、幂等重放、故障注入后自愈 |

**单测不放这里**——Go 惯例是同目录 `xxx_test.go`。

---

## 四、`warehouse/` 分层

| 目录 | 层 | 干什么 |
|---|---|---|
| `spark/etl/ods/` | **ODS** | 原样抽取：业务库 → Parquet（不做任何加工，只加日期分区） |
| `spark/etl/dwd/` | **DWD** | 清洗、关联、打宽：一个事件一行，维度都补齐 |
| `spark/etl/dws/` | **DWS** | 按主题聚合：按课程/学生/日期算指标 |
| `spark/etl/ads/` | **ADS** | 面向应用：报表和 Agent 直接读的结果表 → 回写 ClickHouse |
| `spark/quality/` | 质量 | 行数对账、主键唯一、非空率校验 |
| `flink/sql/` | 实时 | Kafka source → 滚动窗口聚合 → Redis + ClickHouse |
| `ddl/` | 表结构 | Parquet 目录约定 + ClickHouse 建表语句 |

**为什么四层要分开**：每层的职责不同——ODS 保证可回溯、DWD 保证口径统一、
DWS 保证复用、ADS 保证查询快。混在一起就变成"一张大表啥都往里塞"。

---

## 五、「新文件该放哪」决策树

照顺序问，第一个命中的就是答案。

```
我要加一段代码
│
├─ 它是"应该做什么"的规格（需求/接口/验收标准）吗？
│     → specs/NNN-功能名/
│
├─ 它是"为什么这么做"的说明吗？
│     → docs/（架构/技术栈/笔记）
│
├─ 它是一个可执行程序的入口（main 函数）吗？
│     → backend/cmd/<api|consumer|agent|migrate>/
│
├─ 它直接解析 HTTP 请求 / 写响应吗？
│     → 对应域的 controller.go
│
├─ 它是业务规则、事务编排、跨多个数据源的组合吗？
│     → 对应域的 service.go
│
├─ 它是 SQL 查询 / 写入吗？
│     → 对应域的 dao.go
│
├─ 它是数据库表结构 / 领域枚举吗？
│     → 对应域的 model.go（表结构改 backend/migrations/）
│
├─ 它是 Redis 库存扣减的 Lua 脚本吗？
│     → backend/internal/enrollment/stock.go
│
├─ 它是 Kafka 事件结构吗？
│     → backend/internal/events/
│
├─ 它是选课规则校验（先修/学分/冲突）吗？
│     → backend/internal/rules/
│
├─ 它是跨域复用的基础设施（DB/缓存/MQ/日志/配置）吗？
│     → backend/internal/platform/
│
├─ 它是 LLM 调用吗？
│     → backend/internal/llm/
│
├─ 它是 Agent 的工具 / 护栏 / 评测吗？
│     → backend/internal/agent/
│
├─ 它是离线数仓的加工 SQL 吗？
│     → warehouse/spark/etl/<层>/
│
├─ 它是实时窗口聚合吗？
│     → warehouse/flink/sql/
│
├─ 它是前端页面（路由目标）吗？
│     → frontend/src/pages/
│
├─ 它是前端可复用组件吗？
│     → frontend/src/components/
│
├─ 它是前端接口调用或类型定义吗？
│     → frontend/src/api/
│
├─ 它是容器/编排/反代/监控配置吗？
│     → deploy/
│
├─ 它是压测脚本或对账脚本吗？
│     → benchmark/
│
└─ 都不匹配 → 【停下来，在群里问】
```

---

## 六、命名规范

| 类型 | 规范 | 例子 |
|---|---|---|
| Go 包名 | 小写单词，不用下划线、不用复数 | `enrollment`、`ratelimit` |
| Go 文件 | 领域名，不加下划线 | `enrollment.go`、`stock.go` |
| Go 测试 | 同目录 + `_test.go` | `stock_test.go` |
| TypeScript 组件 | PascalCase | `CourseCard.tsx` |
| TypeScript 非组件 | camelCase | `useSSE.ts`、`enrollment.ts` |
| SQL 迁移 | 三位数字前缀 | `001_init_schema.sql` |
| 规格目录 | 三位数字 + kebab-case | `002-enrollment-core/` |
| k6 脚本 | 场景名 | `spike_10000.js`、`steady_10min.js` |
| 数仓 SQL | 层_主题 | `dws_offering_stat.sql` |

---

## 七、空文件夹与 `.gitkeep` 约定

**Git 不跟踪空目录。** 所以骨架里的叶子目录都放了一个 `.gitkeep` 占位。

规则：

- 目录里放入**第一个真实文件**后，把 `.gitkeep` 删掉
- 不要把 `.gitkeep` 里写内容（它不是 README）
- 目录的用途写在**本文档**里，不写在 `.gitkeep` 里

---

## 八、依赖方向（可以写进 lint 的规则）

```
                    cmd/*（装配，可依赖任意包）
                            │
        ┌───────────────────┼────────────────────┐
        ▼                   ▼                    ▼
     server/            controller/          consumer 相关
        │                   │
        ▼                   ▼
   middleware/          service/ ──────► dao/ ──────► model/
                            │
                 ┌──────────┼──────────┐
                 ▼          ▼          ▼
              rules/    platform/    events/
```

**三条不许违反**：

| ❌ 禁止 | 为什么 |
|---|---|
| `controller` 直接 import `dao` | 绕过 service 等于绕过事务边界和业务校验，迟早超卖 |
| `dao` 里写业务判断 | 把"名额够不够"写进 SQL 层，就没法重构和测试 |
| `model` import 任何 internal 包 | 它是最底层，反向依赖会成环 |

自查命令（M3 会变成 CI 硬校验）：

```bash
cd backend
go list -deps ./internal/enrollment | grep -E 'platform|rules|events'   # 只应出现这些
```

---

## 九、按目录划分任务

**原则：目录即边界。** 一个人负责一组目录，跨目录改动必须提前告知。

> 分工在骨架搭完后由组长分配，原则是**按目录切、不按功能切**——
> 按功能切（你做"选课"、他做"退课"）必然同文件冲突；按目录切物理上不可能冲突。

| 块 | 目录 | 特点 |
|---|---|---|
| 在线层核心 | `internal/enrollment/`、`rules/`、`benchmark/` | 最硬的活，通常组长扛 |
| 数据与接入 | `internal/platform/`、`course/`、`student/`、`cmd/`、`deploy/` | 量大但规整 |
| 数据开发 + 前端 | `warehouse/`、`frontend/`、`internal/agent/`、`llm/` | 独立性强 |

共享目录（`platform/`、`go.mod`、`deploy/`、`warehouse/ddl/`）改动前在群里说一句。
