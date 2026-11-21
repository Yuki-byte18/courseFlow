# courseFlow Agent 服务

六层架构的业务 Agent。Python + FastAPI，自带 sandbox 与双通道工具执行。

```
┌─────────────────────────────────────────────────────────────┐
│  evaluation   评测层   自建 trace（MySQL）+ LangSmith 可选    │
├─────────────────────────────────────────────────────────────┤
│  execenv      执行环境  Runtime / 工具上下文 / 审批闸门        │
├─────────────────────────────────────────────────────────────┤
│  orchestration 编排层   意图守卫 → 任务图 → supervisor        │
│                        → query / compute / execute / notify  │
├─────────────────────────────────────────────────────────────┤
│  memory       记忆层   上下文窗口（长期记忆待设计）            │
├─────────────────────────────────────────────────────────────┤
│  tools        工具层   L1 注册表 / L2 策略 / L3 双通道        │
│                        L4 治理（审计·预算·熔断·轨迹）         │
├─────────────────────────────────────────────────────────────┤
│  inference    推理层   DeepSeek 客户端（BYOK）                │
└─────────────────────────────────────────────────────────────┘
```

依赖方向严格单向：上层可以 import 下层，下层绝不 import 上层。

---

## 快速启动

```bash
cd agent
python -m pip install -r requirements.txt

# 配置（可选，都有默认值）
copy .env.example .env

# 起服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

服务只监听 127.0.0.1，**不接受浏览器直连**。前端经 Go 的
`/api/v1/agent/*` 代理进来，Go 会带上 `X-Internal-Token` 与已认证用户身份。

健康检查：

```bash
curl http://127.0.0.1:8090/agent/health
```

---

## 工具层：双通道安全模型

每个工具在注册时声明两个治理属性：

| 属性 | 取值 | 含义 |
|---|---|---|
| `side_effect` | `true` / `false` | 会不会改变数据 |
| `exposure` | `direct` / `sandboxed` / `both` | 能被哪条通道调用 |

注册表强制两条**不变量**（在 `ToolSpec.__post_init__` 里，违反就启动失败）：

1. `side_effect=True` ⇒ `exposure ∈ {direct, both}`
   沙箱的 `call_tool` 只桥接只读工具，写工具标成 sandboxed 等于声明一个永远调不到的能力。
2. `exposure ∈ {sandboxed, both}` ⇒ `side_effect=False`
   同一条规则的另一面：沙箱内绝不能执行写操作。

这两条在三个地方各挡一次：注册表、策略层断言、CodeAct 运行时检查。

### 两条通道

**直接通道（function calling）**

```
模型点名工具 → 参数校验 → 权限检查 → 预算检查 → 写操作进审批闸门
             → 宿主执行（线程 + 超时） → 结果截断回灌
```

**代码通道（CodeAct）**

```
模型写代码 → AST 白名单校验 → 子进程沙箱执行
          → 沙箱内 call_tool 桥接回宿主（仅只读工具）
          → 只有 print() 的输出回灌
```

### 什么时候用哪条

写在系统提示词里（`llm/prompts.py`）：

| 走 direct | 走 CodeAct |
|---|---|
| 任务简单、一步完成 | 多步处理（先查再筛再算） |
| 安全性要求高、需单独审批 | 计算密集（统计、排序、求交集） |
| 用户指令明确，对号入座 | 需要跨多次查询编排结果 |
| **任何写操作** | — |

---

## 模型永远拿不到 SQL

这一点不是"约定"，是架构上做不到：**没有"执行 SQL"这个工具**。

模型能看到的只有 17 个工具的名字和 JSON Schema 参数。SQL 写在工具实现里，
是常量字符串 + `%s` 占位符，模型给的值只能落到参数位：

```python
# app/tools/business/query.py
rows = db.fetch_all(
    "SELECT ... FROM offerings o WHERE o.term_id = %s AND c.name LIKE %s",
    (term["id"], common.like_pattern(keyword)),   # ← 模型的值只到这里
)
```

三道防线：

| 层 | 挡住什么 |
|---|---|
| **参数校验**（`direct.py::validate_arguments`） | 类型不符直接拒绝（`offering_id` 收不到 `"1 OR 1=1"`） |
| **`%s` 占位符** | 值不可能改变 SQL 结构 |
| **`db._guard`** | 拒绝含分号的语句，堵住多语句注入 |

沙箱里还堵了一层：`astguard` 的节点白名单**不允许 `import`**，
所以模型写的代码连 `pymysql` 都拿不到，只有 `call_tool(name, args)` 一条通道。

回归测试：`python scripts/check_injection.py` —— 6 种 payload + 类型/枚举/沙箱绕过，全过。

---

## 沙箱：能挡住什么，挡不住什么

`tools/sandbox.py` 用**独立进程**执行模型写的代码：

- AST 白名单（`tools/astguard.py`）：不认识的语法节点直接拒绝，不允许 `import`／文件／网络／`eval`／dunder 属性
- 进程隔离：死循环可 `terminate`，输出截断，工具调不到写操作
- 只有 `print()` 的内容会回灌给模型

**诚实说明**：这不是容器级隔离。它能挡住 import、文件、网络、死循环和内存炸弹，
但挡不住恶意构造的 C 扩展利用。生产环境应该换成 gVisor / Firecracker / 容器。
这一条写在这里，不藏着。

---

## 编排：意图守卫 + 任务图

**入口守卫**（`orchestration/intent.py`）只做粗粒度三分类：

- `query_compute` 查询 / 计算
- `write` 写操作
- `meta` 闲聊 / 元问题

**置信度低于 0.75 触发澄清，不硬猜。** 硬猜的代价是：用户问"怎么选课"（meta），
系统却真的去选了一门课。

**Supervisor**（`orchestration/supervisor.py`）：

```
意图识别 → 低置信度澄清并结束
        → meta 直接回答
        → 产粗粒度任务图（graph 事件下发）
        → 循环：取可执行节点 → 子 agent → 记录观察 → 更新图
        → 图跑完后问一次"还需不需要补节点"（运行时增删，dynamic=true）
        → 汇总观察 → 流式最终回答
```

四个子 agent 的工具集互相隔离（`owner` 字段决定）：

| agent | 能做什么 | 工具数 |
|---|---|---|
| `query` | 查课程、班次、余量、课表、已选、轮次 | 7 |
| `compute` | 学分核算、冲突检测、余量排行、空余时段、候选筛选 | 5 |
| `execute` | 选课、退课（**唯一的写工具**） | 2 |
| `notify` | 生成通知卡、保存/查询提醒 | 3 |

---

## 写操作：为什么 Agent 不直接写 MySQL

`tools/business/execute.py` 里的选课/退课**不执行 SQL**，而是带着
**用户自己的 JWT** 回调 Go 的 `POST /my/enrollments`。

三个理由：

1. **规则只有一份**。容量扣减、学分上限、时间冲突、防超卖都在 Go 的事务里。
   Agent 再实现一遍一定会漂移，而"两处各判一次"的结果往往是超卖。
2. **身份不放大**。Agent 用这个学生自己的凭据，不可能替别人选课。
3. **审计链完整**。Go 侧照常记录这次选课，看起来和用户自己点按钮没有区别。

写操作还要过**审批闸门**：`execenv/runtime.py` 发 `approval_required` 事件并挂起，
前端弹审批面板，用户点确认后走 Go 的 `/agent/approvals/{id}/decide` 转发回来。
超时自动拒绝——风险不对称，宁可失败。

---

## 协议：AG-UI

内部事件（`app/events.py`）表达**领域语义**；前端消费的是 **AG-UI** 标准事件。
两者之间有一层纯函数式的适配器 `app/protocol/agui.py`。

为什么要分两层：
- 协议是对外契约，前端只认 AG-UI，不再理解 `agent_started` / `graph` 这些内部概念
- 内部想改事件粒度时只改映射，前端一行不动

覆盖的 AG-UI 事件：

| 事件 | 用途 |
|---|---|
| `RUN_STARTED` / `RUN_FINISHED` / `RUN_ERROR` | 生命周期（带 threadId / runId） |
| `STATE_SNAPSHOT` / `STATE_DELTA` | **整棵 Agent 状态树**（JSON Patch） |
| `TEXT_MESSAGE_START` / `_CONTENT` / `_END` | 流式文本 |
| `TOOL_CALL_START` / `_ARGS` / `_END` | 工具调用（参数也流式） |
| `STEP_STARTED` / `STEP_FINISHED` | 子 agent 与代码执行的步骤 |
| `CUSTOM` | 领域事件（卡片、审批、预算、会话绑定） |

**状态树**（前端 `AgentState`，与 `AgUiEncoder.state` 一一对应）：

```json
{
  "intent":   { "intent": "query_compute", "confidence": 0.92, "reason": "..." },
  "nodes":    [ { "id": "n1", "agent": "query", "status": "running", "dynamic": false } ],
  "agents":   { "n1": { "agent": "query", "items": [ { "tool": "search_offerings" } ] } },
  "approval": { "approval_id": "...", "tool": "enroll_offering", "timeout_s": 240 },
  "cards":    [ { "card": "offering_list", "payload": {} } ],
  "budget":   null
}
```

前端只需要一个 reducer：收到 `STATE_SNAPSHOT` 建树，收到 `STATE_DELTA` 打补丁。
`STATE_DELTA` 用「整棵子树 replace」而不是最小 diff —— 状态树只有几 KB，
算精确 diff 的复杂度远大于收益。

---

## 对话持久化

两层结构，迁移 `0008_agent_conversation.sql`：

| 表 | 作用 |
|---|---|
| `agent_threads` | 一个会话（= AG-UI 的 `threadId`），带标题、条数、最后活跃时间 |
| `agent_messages` | 会话里的消息，assistant 消息带 `cards_json` |

写入时机（`app/memory/conversation.py`，由 `Runtime` 调用）：

1. **运行开始** → 建会话 + 存用户消息（先落库再发事件，否则刷新就丢）
2. **运行结束** → 存助手消息 + 本轮所有卡片

历史消息在**下一次运行前自动加载**（`load_history`），所以：
- 刷新页面 → 前端从 `/agent/threads/{id}/messages` 读回来
- 换设备 → 同一个 threadId 能接上上下文
- 前端不必自己维护上下文，后端才是唯一真相

写入失败只记警告，不影响主流程 —— 对话持久化是观测能力，不是业务路径。

---

## 前端

### Markdown 渲染（零依赖）

`components/Markdown/` 自己实现的解析器：

- 块级：标题、段落、代码块（带语言标签与复制）、有序/无序列表、引用、表格、分割线
- 行内：行内代码、粗体、斜体、链接
- **渲染成 React 元素而不是 `innerHTML`** —— 模型输出是不可信内容，
  这一点比自己写解析器更重要
- 流式友好：未闭合的 ``` 也能正常渲染，不会因为"还没写完"而乱掉

### 对话页

```
┌──────────┬──────────────────────────────────┐
│ 新建对话  │ 步骤条（意图 + 任务图节点状态）    │
│          ├──────────────────────────────────┤
│ 会话 1   │ Markdown 消息（流式光标）          │
│ 会话 2   │ 子 agent 卡片（工具/沙箱徽标）      │
│ 会话 3   │ 审批面板（倒计时）                 │
│          │ 业务卡片                          │
│          ├──────────────────────────────────┤
│          │ 输入框                            │
└──────────┴──────────────────────────────────┘
```

### Trace 面板

`/agent/trace`：顶部一排**评估指标**（成功率 / 澄清率 / 平均步数 / 工具调用 / 代码执行 / 写操作 / 审批），
下面是运行列表，点开是逐步轨迹。

**观测 vs 评估**：观测回答"这一次发生了什么"，看的是 trace 明细；
评估回答"整体靠不靠谱"，看的是聚合指标。两者互补，缺一不可。

---

## 目录

```
app/
  main.py            入口：注册工具 → 自检 → 冻结注册表 → 起 HTTP
  config.py          全部环境变量 + .env 加载
  db.py              只读为主 + ping()
  events.py          内部事件类型与总线（线程安全）
  sse.py             SSE 编码
  api/routes.py      /chat（SSE + AG-UI）/ approvals / runs / health
  protocol/agui.py   内部事件 → AG-UI（含状态树）
  llm/               client.py（DeepSeek）· prompts.py（含双通道准则）
  tools/             spec · registry · context · policy · direct · codeact
                     astguard · sandbox · business/{query,compute,execute,notify}
  governance/        budget · breaker · core（审计）
  memory/            context.py（窗口裁剪）· conversation.py（对话持久化）
  orchestration/     intent · graph · agents · supervisor
  execenv/           runtime.py（Runtime + 审批闸门）
  evaluation/        tracer.py（trace）· metrics 由 Go 侧聚合 · langsmith_hook.py
scripts/sync_env.py  从 backend 配置派生 .env
```

---

## 评测层

**自建 trace**（`app/evaluation/tracer.py`）：运行结束后把事件写进
`agent_runs` / `agent_steps`（迁移 `0007_agent_trace.sql`）。
前端 Trace 面板从 Go 的 `/api/v1/agent/runs` 读，刷新页面、重启服务都还在。

为什么自己存一份：第三方服务需要网络和 Key，答辩现场不可依赖；
而且我们要在自己的界面里展示，跨系统拼数据很麻烦。

**LangSmith**（`app/evaluation/langsmith_hook.py`）：可选。
没有 `LANGSMITH_API_KEY` 或 `AGENT_LANGSMITH_ENABLED=false` 时**完全不导入** langsmith——
不能让一个观测依赖变成启动依赖。

```bash
AGENT_LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=lsv2_...
AGENT_LANGSMITH_PROJECT=courseflow-agent
```

---

## 配置

见 `.env.example`。关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_INTERNAL_TOKEN` | `dev-internal-token-change-me` | 必须与 Go 侧一致，否则一律 401 |
| `AGENT_GO_BASE_URL` | `http://127.0.0.1:8080` | 写操作回调用 |
| `AGENT_MYSQL_*` | root@127.0.0.1/courseflow | 工具层直接读业务库 |
| `AGENT_INTENT_THRESHOLD` | `0.75` | 低于它就澄清 |
| `AGENT_MAX_TOOL_CALLS` | `24` | 预算 |
| `AGENT_SANDBOX_TIMEOUT_S` | `10` | 代码执行超时 |
| `AGENT_APPROVAL_TIMEOUT_S` | `240` | 审批超时自动拒绝 |

---

## 目录

```
app/
  main.py            入口：注册工具 → 自检 → 冻结注册表 → 起 HTTP
  config.py          全部环境变量，都有默认值
  db.py              只读为主；写操作只在 notify 里（自己的表）
  events.py          事件类型与总线（线程安全）
  sse.py             SSE 编码
  api/routes.py      /chat（SSE）/ approvals / runs / health
  llm/               client.py（DeepSeek）· prompts.py（含双通道准则）
  tools/             spec · registry · context · policy · direct · codeact
                     astguard · sandbox · business/{query,compute,execute,notify}
  governance/        budget · breaker · core（审计）
  memory/            context.py（窗口裁剪）+ 长期记忆接口占位
  orchestration/     intent · graph · agents · supervisor
  execenv/           runtime.py（Runtime + 审批闸门）
  evaluation/        tracer.py · langsmith_hook.py
```

---

## 已知边界

- **记忆层只实现了上下文窗口 + 对话持久化**，长期记忆（语义召回/遗忘策略）等设计定稿
- **审批要求浏览器保持连接**：页面关掉等于放弃这次审批（超时自动拒绝）
- **沙箱是进程级不是容器级**（见上）
- **教师角色暂时没有 `agent:chat`**：权限点已就位，但迁移 0002 只授了 admin 和 student
- **流式只在最终回答**：中间工具轮次是非流式的（解析成本高、收益低）
- **评估目前是聚合指标**，还没有 LLM-as-judge 的质量评分

## 踩过的坑（写下来避免重犯）

1. **payload 里的 `kind` 会覆盖事件类型**
   `Event.to_dict()` 是 `{"kind": 事件类型, **payload}`。意图事件的 payload 原来也有
   一个 `kind` 字段，结果事件类型被覆盖，前端完全认不出来。改成 `intent`。

2. **`__builtins__` 在模块里是 dict 不是模块**
   沙箱构建受限 builtins 时用了 `hasattr(__builtins__, "len")`，永远为 False，
   导致沙箱里一个内置函数都没有。必须用 `import builtins` 拿真模块。

3. **`port` 在 yaml 里出现两次**
   `server.port`（8080）和 `mysql.port`（3306）。直接正则抓 `port` 会让 Agent
   去连 8080 上的 MySQL。`sync_env.py` 改成按段解析。

4. **PowerShell 的 `Set-Content -Encoding utf8` 会写 BOM**
   BOM 会污染 `.env` 第一行的键名。改用 Python 写文件，并显式 `newline="\n"`。
