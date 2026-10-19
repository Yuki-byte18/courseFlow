# courseFlow

**用"高校选课 / 抢课"这个真实业务，跑通高并发后端、在线/近线/离线三层数据架构、AI Agent 接入业务三件事。**

> 状态：**M0 骨架搭建中**（目录、文档、配置就绪；业务代码从 M1 开始）
> 这是三个人一学期的课程项目，也是一份求职作品。

---

## 这个项目想证明什么

选课抢课是典型的**瞬时洪峰**场景：开抢那 30 秒内有几千人同时抢几十个名额。

| 目标 | 怎么证明 |
|---|---|
| **高并发后端** | 压测报告：基线 vs 优化后的 QPS / p99 对比；**超卖数 = 0**（对账脚本证明） |
| **在线 / 近线 / 离线架构** | 三条链路都有可运行的代码和真实数据产出，延迟量级分别是 ms / 秒 / 分钟 |
| **Agent 接入业务** | 学生用大白话问"还有哪些三学分的课"，Agent 查真数据、返回可点击的卡片 |
| **数据开发** | ODS → DWD → DWS → ADS 四层数仓，Spark 离线 + Flink 实时 + ClickHouse 查询 |

**每个技术点都必须能回答三个问题：为什么用它、有什么数据、代价是什么。** 答不上来的不写进简历。

---

## 架构一眼看懂

```
前端：一套 React 应用，三组路由
  /student/*  学生端      /teacher/*  教师端（只读）      /admin/*  管理端
                    │
                    ▼
在线层 ─ api（抢课/查课，毫秒级，不可降级）
      └ agent（对话/查询，秒级，只读，可降级）
                    │ 领域事件 → Kafka
                    ▼
近线层 ─ consumer（Go：异步批量落库 + 对账）
      └ Flink（10s 滚动窗口 → 实时热榜 / 成功率 / 库存大盘）
                    │ 每天凌晨批量抽取
                    ▼
离线层 ─ Spark → ODS → DWD → DWS → ADS（Parquet 分区）
                    产出：报表、课程画像、学生画像

存储 5 个角色：
  MySQL（业务真相）· Kafka（事件总线）· Redis（库存/限流）
  Parquet（数仓四层）· ClickHouse（OLAP 查询）
```

完整说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Go 1.26 · Gin · GORM · Viper · zap · sarama · goose |
| 数据开发 | Spark (PySpark) · Flink · ClickHouse · Parquet |
| 前端 | React 19 · Vite 8 · TypeScript 6 · Ant Design · TanStack Query · ECharts |
| 存储 | MySQL 8.4 · Redis 7.4 · Kafka 3.9 (KRaft) · ClickHouse |
| 基础设施 | Docker Compose · Nginx · Prometheus · Grafana |
| 压测 | k6 + wrk |

选型理由和"明确不引入什么"见 [`docs/TECH_STACK.md`](docs/TECH_STACK.md)。

---

## 仓库结构

```
courseFlow/
├── backend/      Go 后端（在线层 + 近线层的落库部分）
│   ├── cmd/      可执行程序：api / consumer / agent / migrate
│   └── internal/ platform（基础设施）· events（事件契约）· 各业务域
├── warehouse/    数据开发：Spark 离线四层 + Flink 实时 + 数仓 DDL
├── frontend/     React 前端（一套应用，三组路由）
├── specs/        规格：先写这里，再写代码（SDD）
├── docs/         工程文档（宪章 / 目标 / 架构 / 技术栈 / 协作规范 / 目录分层）
├── deploy/       Docker Compose · Nginx · Prometheus · Grafana
└── benchmark/    k6 压测脚本 + 对账脚本 + 报告
```

每个目录属于架构的哪一块、文件该放哪，见 [`docs/PROJECT_LAYOUT.md`](docs/PROJECT_LAYOUT.md)（含一张"新文件该放哪"决策树）。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/CONSTITUTION.md`](docs/CONSTITUTION.md) | **项目总宪法**——写代码前必读的十条铁律（给人和 AI 都看） |
| [`docs/GOALS.md`](docs/GOALS.md) | 项目目标、优先级、明确的非目标 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 整体架构、数据流、关键设计决策 |
| [`docs/TECH_STACK.md`](docs/TECH_STACK.md) | 技术选型与理由 |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | SDD 工作流、分支规范、PR 规范 |
| [`docs/PROJECT_LAYOUT.md`](docs/PROJECT_LAYOUT.md) | 目录分层与归属 |
| [`specs/README.md`](specs/README.md) | 规格工作流（先规格后代码） |

---

## 快速开始

> **当前是 M0 骨架阶段，还没有可运行的业务代码。** 下面命令在第一个 API 端点完成后可用。

运行环境是 **Linux**（虚拟机即可）。

```bash
# 1. 配置环境变量
cp .env.example .env        # 填入真实值，.env 不要提交

# 2. 启动依赖服务
docker compose -f deploy/docker-compose.yml --env-file .env up -d

# 3. 后端
cd backend
go mod tidy
go run ./cmd/api

# 4. 前端
cd frontend
pnpm install
pnpm dev                    # http://localhost:5173
```

前端的 `/api` 请求会通过 Vite 代理转发到 `http://127.0.0.1:8080`，
**所以后端不需要为了开发环境开放 CORS**。

---

## 开发进度

- [x] **M0** 目录骨架、六份工程文档、配置/日志/健康检查、依赖编排
- [ ] **M1** 业务闭环 v1（朴素实现）+ 性能基线
- [ ] **M2** 高并发改造：Redis Lua 扣减 / Kafka 削峰 / 幂等 / 限流排队
- [ ] **M3** 压测体系与可观测（Prometheus / Grafana / pprof）
- [ ] **M4** 在线 / 近线 / 离线三层落地（Spark + Flink + ClickHouse）
- [ ] **M5** 业务 Agent（只读工具 + 确认卡片 + 评测）
- [ ] **M6** 课程交付：文档、演示、答辩
- [ ] **M7** 简历包装与复盘

---

## 协作

三个人、一个学期、规格先行（SDD）。分支命名、提交信息、PR 要求见
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)。

**开工前请先读 [`docs/CONSTITUTION.md`](docs/CONSTITUTION.md)**——那里是硬规则。

---

## 安全说明

- `.env`、`backend/configs/config.yaml`、任何密钥**都不入库**
- 仓库只保留 `*.example` 模板
- 本项目的选课数据均为**模拟数据**，不对接任何真实教务系统
