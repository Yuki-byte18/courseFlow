# 项目技术栈（TECH_STACK）

> 每一项都写清 **用它干什么 / 为什么是它 / 不用什么**。
> 答不出"为什么是它"的技术，不许引入。

---

## 一、后端

| 用途 | 选型 | 为什么是它 |
|---|---|---|
| 语言 | **Go 1.26** | 并发模型简单（goroutine）、静态编译（镜像小）、适合写高并发服务 |
| HTTP 框架 | **Gin** | 生态最大、中间件机制简单、性能够 |
| ORM | **GORM** | CRUD 和查询写得快；**但热路径不用它**（见下） |
| 配置 | **Viper** | YAML + 环境变量 + 默认值，优先级清晰 |
| 日志 | **zap** | 结构化、**支持采样**（压测时这很关键） |
| Kafka 客户端 | **`IBM/sarama`** | 用得最多、资料最全；性能和功能都够 |
| Redis 客户端 | `redis/go-redis/v9` | 事实标准 |
| 数据库迁移 | **`pressly/goose`** | SQL 优先、支持 `embed.FS` 打进二进制 |
| 鉴权 | `golang-jwt/jwt/v5` | 标准 |
| 参数校验 | Gin 内置 `binding`（go-playground/validator） | 不用额外引 |
| 熔断 | `sony/gobreaker` | 简单够用 |
| 单机限流 | `golang.org/x/time/rate` | 官方扩展库 |
| 分布式限流 | **自研 Redis Lua 令牌桶** | 正好是学习点，比引框架学到的多 |
| 指标 | `prometheus/client_golang` | 标准 |
| 测试 | testify + `httptest` + 手写 fake | 手写 fake 比 mock 框架透明 |
| 热重载（开发） | `air` | 改代码自动重启，省大量时间 |
| 依赖注入 | **不用**，手写装配 | 4 个 main.go 各 30 行，透明比自动好 |

### 两条重要的使用纪律

**① GORM 只在 CRUD 用，热路径手写 SQL**

| 场景 | 用什么 | 为什么 |
|---|---|---|
| 管理端/教师端 CRUD、分页 | GORM | 反射开销在这里无所谓 |
| 课程组合筛选 | GORM `Session` 拼条件 | 可读 |
| **异步批量落库** | **手写批量 SQL** | GORM 每行都走反射和回调，压测时是明显瓶颈 |
| 复杂聚合 / 对账 | 原生 SQL | GORM 表达不清晰，还容易 N+1 |

**② 禁止 `AutoMigrate`**

不是它不专业，而是**这个项目的正确性红线就写在 DDL 里**：

```sql
-- 这条唯一索引是"同一学生不能重复选同一门课"的最后一道闸
ALTER TABLE enrollments ADD UNIQUE KEY uk_student_offering (student_id, offering_id);
```

AutoMigrate 会帮你生成索引，但你**看不见生成的 DDL**，也没法审。
所有 DDL 走 `backend/migrations/*.sql`，能拿去答辩讲。

---

## 二、数据开发

| 用途 | 选型 | 为什么是它 |
|---|---|---|
| 离线计算 | **Apache Spark**（本地模式 `local[4]`）+ **Spark SQL** | 离线计算最主流；SQL 表达分层天然；**不需要 Hadoop 集群** |
| 离线语言 | **PySpark** | Python 门槛低，不用碰 sbt/maven |
| 实时计算 | **Apache Flink**（本地模式单作业）+ **Flink SQL** | 实时计算国内主流；窗口聚合是它的主场 |
| 运行时 | **OpenJDK 21** | Spark / Flink 都要 JVM |
| 数仓存储 | **Parquet**，按 `dt` 分区 | 列式、便宜、就是数仓该有的样子 |
| OLAP 服务 | **ClickHouse** | 高并发聚合查询，MySQL 干这个会拖垮 OLTP |
| 调度 | **cron + `spark-submit` / `flink run`** | 够了 |
| 数仓建模 | **ODS → DWD → DWS → ADS** 四层 | 大厂标准分层 |

### 为什么不上 Hive / HDFS / YARN

这三个在 2026 年**已经不是"最主流"了**：

| 组件 | 现状 | 判断 |
|---|---|---|
| Hive on MapReduce | 已被 Spark 取代 | ❌ 不值得 |
| HDFS | 正被对象存储 / 本地文件系统取代 | ❌ 重，1.5 GB 内存 |
| YARN | 正被 K8s 取代 | ❌ 很重 |
| DolphinScheduler | 好工具，但 ZK + 多个 Java 服务 | ❌ cron 够用 |

而且 **16 GB 的虚拟机装不下全套**（HDFS+YARN+Hive+Spark+Flink+DS 约 19–21 GB，几乎全是 JVM）。

**面试如果被问"为什么不用 Hive"，这个答案本身就是加分项**——它证明你会算账，不是无脑追组件。

### Spark / Flink 分别是干什么的

```
Flink（实时，常驻）
  Kafka 事件 → 10 秒滚动窗口 → 实时热榜 / 秒级成功率 / 库存大盘
            → Redis（秒级读数）+ ClickHouse（可查询）

Spark（离线，每天凌晨跑一次就退出）
  业务库 → Parquet ODS → DWD → DWS → ADS
        → ClickHouse（报表查询）
        → Agent 上下文（画像）
```

**两者的关键区别**：Flink 关心"现在正在发生什么"，Spark 关心"过去有什么规律"。

### 必须搞懂的概念（面试会问）

- **Flink**：水位线（watermark）、滚动窗口 vs 滑动窗口、checkpoint、状态后端
- **Spark**：DataFrame 与 SQL 的关系、Parquet 分区裁剪、宽依赖 vs 窄依赖（了解即可）
- **数仓**：为什么要分层（ODS 原样、DWD 清洗、DWS 聚合、ADS 面向应用）

---

## 三、前端

| 用途 | 选型 | 为什么是它 |
|---|---|---|
| 框架 | **React 19** | |
| 构建 | **Vite 8** | 冷启动秒级，HMR 快 |
| 语言 | **TypeScript 6** | 后端 20+ 接口，靠类型兜住 |
| 路由 | **react-router v7** | 三组路由 + 角色守卫的标准方案 |
| **服务端状态** | **TanStack Query** | 抢课结果轮询、余量自动刷新、缓存失效——正是它的主场 |
| 全局状态 | **不用 Redux / 不用 Zustand** | 登录态用 Context + Query 缓存就够 |
| UI 库 | **Ant Design v5** | 中文生态、表格/表单/分页开箱即用 |
| 表单 | **react-hook-form + zod** | 管理端排课表单字段多 |
| 图表 | **ECharts** | 大屏效果好、性能好、中文文档 |
| 实时推送 | **原生 EventSource（SSE）** | 浏览器自带 |
| 课表周视图 | **自研 CSS Grid** | AntD 没有 timetable 组件，自己画更自由 |
| 接口类型 | **`openapi-typescript`** 从 `specs/*/openapi.yaml` 生成 | 后端改字段前端立刻报红 |
| 样式 | AntD 组件 + **CSS Modules** | 不引 Tailwind（和 AntD 样式系统冲突） |

**一个诚实的取舍**：AntD 会让包体积到 300 KB+（gzip 后）。对内部教务系统完全无所谓。

---

## 四、基础设施与工具

| 用途 | 选型 | 说明 |
|---|---|---|
| 容器编排 | **Docker Compose** | 明确不上 K8s（三个人、单机） |
| 接入层 | **Nginx** | 静态托管 + 反代 + IP 级粗限流；**不做独立网关** |
| 监控 | **Prometheus + Grafana** | |
| 链路追踪 | **request_id + 结构化日志** | OpenTelemetry 列入"有余力再说" |
| 性能剖析 | Go 内置 `pprof` | |
| **压测** | **k6 为主 + wrk 交叉验证** | 见下 |
| Lint | `golangci-lint`（含 depguard 守依赖方向） | M3 接 CI |
| CI | GitHub Actions | build / vet / test -race / lint |
| 运行环境 | **Linux 虚拟机（8 核 16 GB）** | Kafka/Flink/Spark 在 Linux 上是原生一等公民 |

### 为什么压测要两个工具

- **k6**：能描述"0 → 10000 用户 1 秒内阶跃"这种场景，指标全，能推 Prometheus。**场景化压测靠它**
- **wrk**：C 写的，单机极限 QPS 高得多。**因为 k6 是 JS + 单进程模型，压测端自己可能先成为瓶颈**

交叉验证的意义：如果 k6 测出 5000 QPS 而 wrk 测出 9000，说明 k6 端受限；
两个都说 5000，那 5000 就可信。**压测报告里写清这件事，可信度立刻上一个档。**

---

## 五、明确不引入（以及为什么）

| ❌ 不用 | 理由 |
|---|---|
| Kubernetes | 三个人、单机，K8s 会吃掉全部学习时间 |
| 独立 API 网关（Kong / APISIX） | 只有 2 个后端入口，网关解决的问题我们都没有 |
| 向量数据库（Qdrant / Milvus / pgvector） | 课程库约 2000 条、embedding 6 MB，**内存暴力检索毫秒级**，引向量库纯负担 |
| Elasticsearch | 课程搜索用 MySQL `LIKE` 就够，几百条数据 |
| Hive / HDFS / YARN / DolphinScheduler | 见上文：已非主流，且 16 GB 装不下 |
| Redis Cluster / MySQL 主从 | 单机够用。**但要在文档里写清"如何扩展"**——这比真搭一套值钱 |
| gRPC / GraphQL | 全 REST，无跨语言服务 |
| RabbitMQ / RocketMQ | Kafka 已定 |
| Redux / Zustand | 前端状态太简单 |
| Tailwind CSS | 与 AntD 冲突 |
| wire / fx（依赖注入） | 手写装配更透明 |
| OpenTelemetry 全量埋点 | 工作量 3–5 天，收益对本项目有限。先用 request_id 轻量方案 |

---

## 六、版本锁定

| 组件 | 版本策略 |
|---|---|
| Go | `1.26`（写在 `go.mod`） |
| Node / pnpm | `24` / `11`（`.nvmrc` + `package.json` 的 `packageManager`） |
| Python（数据开发） | 用 `venv` 固定一个**受 PySpark 支持**的版本（3.11 / 3.12），别直接用系统的 3.13 |
| MySQL | `mysql:8.4` |
| Redis | `redis:7.4-alpine` |
| Kafka | `apache/kafka:3.9.x` |
| ClickHouse | `clickhouse/clickhouse-server:24.x` |
| Nginx | `nginx:1.27-alpine` |
| Spark / Flink | 锁大版本，装的时候查最新稳定版并记录到文档 |

**所有第三方镜像锁定具体版本，不用 `latest`。** 答辩时能复现的环境才叫环境。
