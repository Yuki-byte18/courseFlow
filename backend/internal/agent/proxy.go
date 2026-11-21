package agent

// 本文件是 Go 侧的「Agent 代理层」：
//   - 把浏览器的对话请求转成 SSE 流转发给 Python Agent 服务
//   - 转发审批决议
//   - 读 trace（agent_runs / agent_steps）给观测面板
//
// 为什么由 Go 代理而不是浏览器直连 Python：
//   1. 鉴权边界只有一个：JWT 只在 Go 这里解析，Python 只接受内部的 X-Internal-Token
//   2. 业务权限在 Go 侧判定，Python 拿到的是"已经授权过的身份"
//   3. 浏览器只需要一个 origin，省掉跨域与 Cookie 的麻烦
//
// 代价是多一跳网络。对 Agent 这种"本来就要等模型好几秒"的场景可以忽略。

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
	"gorm.io/gorm"

	"courseflow/internal/platform/middleware"
	"courseflow/internal/platform/perms"
)

// RuntimeConfig 是 Python Agent 服务的接入参数。
//
// 与 Python 侧一致，先从环境变量读：Agent 架构还在演进，
// 过早固化进 config.yaml 会让每次调整都要改配置文件。
// 等架构定稿再统一收进 Viper。
type RuntimeConfig struct {
	ServiceURL    string
	InternalToken string
	Timeout       time.Duration
}

// RuntimeConfigFromEnv 读环境变量。
//
//	AGENT_SERVICE_URL     默认 http://127.0.0.1:8090
//	AGENT_INTERNAL_TOKEN  必须与 Python 侧 AGENT_INTERNAL_TOKEN 一致
func RuntimeConfigFromEnv() RuntimeConfig {
	cfg := RuntimeConfig{
		ServiceURL:    "http://127.0.0.1:8090",
		InternalToken: "dev-internal-token-change-me",
		Timeout:       5 * time.Minute,
	}
	if v := strings.TrimSpace(os.Getenv("AGENT_SERVICE_URL")); v != "" {
		cfg.ServiceURL = strings.TrimRight(v, "/")
	}
	if v := strings.TrimSpace(os.Getenv("AGENT_INTERNAL_TOKEN")); v != "" {
		cfg.InternalToken = v
	}
	return cfg
}

// runtimeController 承载代理与 trace 读取。
//
// 与 Key 校验的 Controller 分开：那一个不需要数据库，
// 这一个需要，职责也不一样（一个是上游校验，一个是业务代理）。
type runtimeController struct {
	db     *gorm.DB
	cfg    RuntimeConfig
	proxy  *httputil.ReverseProxy
	client *http.Client
}

// NewRuntimeController 构造代理控制器。
func NewRuntimeController(db *gorm.DB, cfg RuntimeConfig) *runtimeController {
	target, err := url.Parse(cfg.ServiceURL)
	if err != nil || target.Host == "" {
		target = &url.URL{Scheme: "http", Host: "127.0.0.1:8090"}
	}

	proxy := httputil.NewSingleHostReverseProxy(target)

	// Director **只在构造时设一次**。
	// 早期版本在每次请求里改 proxy.Director —— 那是数据竞争：
	// 两个并发请求会互相覆盖对方的 Director。
	// 现在的做法是 Director 只固定改写目标路径，请求头由调用方设在 outbound 请求上。
	proxy.Director = func(r *http.Request) {
		r.URL.Scheme = target.Scheme
		r.URL.Host = target.Host
		r.URL.Path = "/agent/chat"
		r.Host = target.Host
	}

	// -1 表示"每次写入后立刻 flush"。SSE 必须有这个，
	// 否则 reverse proxy 会攒一批再发，前端看到的是一段一段蹦出来。
	proxy.FlushInterval = -1
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		zap.L().Error("转发到 Agent 服务失败", zap.Error(err), zap.String("path", r.URL.Path))
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(
			`{"error":{"code":"agent_unavailable","message":"Agent 服务不可用，请确认 agent 进程已启动"}}`,
		))
	}

	return &runtimeController{
		db:     db,
		cfg:    cfg,
		proxy:  proxy,
		client: &http.Client{Timeout: 10 * time.Second},
	}
}

// RegisterRuntime 挂载 Agent 运行时路由。
func (c *runtimeController) RegisterRuntime(authed *gin.RouterGroup) {
	group := authed.Group("/agent")
	group.POST("/chat", middleware.RequirePerm(perms.AgentChat), c.Chat)
	group.POST("/approvals/:id/decide", middleware.RequirePerm(perms.AgentChat), c.DecideApproval)
	group.POST("/clarifications/:id/answer", middleware.RequirePerm(perms.AgentChat), c.AnswerClarification)
	group.GET("/runs", middleware.RequirePerm(perms.AgentChat), c.ListRuns)
	group.GET("/runs/:runId", middleware.RequirePerm(perms.AgentChat), c.GetRun)
	group.GET("/threads", middleware.RequirePerm(perms.AgentChat), c.ListThreads)
	group.GET("/threads/:threadId/messages", middleware.RequirePerm(perms.AgentChat), c.ListMessages)
	group.DELETE("/threads/:threadId", middleware.RequirePerm(perms.AgentChat), c.DeleteThread)
	group.GET("/metrics", middleware.RequirePerm(perms.AgentChat), c.Metrics)
	group.GET("/health", middleware.RequirePerm(perms.AgentChat), c.Health)
}

// ---------------------------------------------------------------------------
// 对话（SSE 透传）
// ---------------------------------------------------------------------------

// Chat 处理 POST /api/v1/agent/chat。
//
// 直接透传请求体里的 {message, model, history}，
// 并把浏览器的 DeepSeek Key 与已认证身份塞进内部请求头。
func (c *runtimeController) Chat(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	// SSE 不能吃 http.Server 的 WriteTimeout：流还在写，连接就被掐了。
	// 清掉本请求的写/读截止时间，让 Agent 跑完。
	if rc := http.NewResponseController(ctx.Writer); rc != nil {
		_ = rc.SetWriteDeadline(time.Time{})
		_ = rc.SetReadDeadline(time.Time{})
	}

	apiKey := strings.TrimSpace(ctx.GetHeader(apiKeyHeader))
	if apiKey == "" {
		respondError(ctx, http.StatusBadRequest, "missing_api_key", "请先在 AI 设置里配置 DeepSeek API Key")
		return
	}

	// 用户自己的 token 原样转发给 Python：
	// 选课/退课这类写操作要带它回调本服务，身份不会放大。
	rawToken := strings.TrimSpace(strings.TrimPrefix(ctx.GetHeader("Authorization"), "Bearer "))

	// 内部请求头设在 outbound 请求上；Director 只负责改写目标地址。
	ctx.Request.Header.Set("X-Internal-Token", c.cfg.InternalToken)
	ctx.Request.Header.Set("X-LLM-Api-Key", apiKey)
	ctx.Request.Header.Set("X-User-Id", strconv.FormatInt(user.ID, 10))
	ctx.Request.Header.Set("X-Username", user.Username)
	ctx.Request.Header.Set("X-Real-Name", user.RealName)
	ctx.Request.Header.Set("X-User-Scopes", strings.Join(user.Perms, ","))
	ctx.Request.Header.Set("X-User-Token", rawToken)
	ctx.Request.Header.Set("Content-Type", "application/json")

	// **SSE 必须清掉这一条连接的写超时。**
	//
	// Go 的 http.Server.WriteTimeout 是从"读完请求头"开始算的**绝对**超时，
	// 写入不会重置它。Agent 一次运行要 30~150 秒（多轮 LLM + 工具 + 审批），
	// 所以只要 WriteTimeout 是个有限值，流一定会在那一刻被硬切：
	// 后端还在跑、日志还在动，浏览器却再也收不到任何数据 —— 用户看到的就是"卡住"。
	//
	// 只对这一个请求生效（http.ResponseController 作用于当前连接），
	// 其它接口仍然受 server.write_timeout 保护。
	if controller := http.NewResponseController(ctx.Writer); controller != nil {
		if err := controller.SetWriteDeadline(time.Time{}); err != nil {
			zap.L().Warn("无法清除 SSE 写超时，将退化为 server.write_timeout", zap.Error(err))
		}
	}

	// 上游只需要 {message, model, history}，路径由 Director 改写
	c.proxy.ServeHTTP(ctx.Writer, ctx.Request)
}

// DecideApproval 处理 POST /api/v1/agent/approvals/:id/decide。
func (c *runtimeController) DecideApproval(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	approvalID := strings.TrimSpace(ctx.Param("id"))
	if approvalID == "" {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "审批 ID 不合法")
		return
	}

	var req struct {
		Approved bool `json:"approved"`
	}
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	endpoint := fmt.Sprintf("%s/agent/approvals/%s/decide", c.cfg.ServiceURL, approvalID)
	payload := fmt.Sprintf(`{"approved":%t}`, req.Approved)

	httpReq, err := http.NewRequestWithContext(
		ctx.Request.Context(), http.MethodPost, endpoint, strings.NewReader(payload))
	if err != nil {
		respondError(ctx, http.StatusInternalServerError, "internal_error", "构造内部请求失败")
		return
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("X-Internal-Token", c.cfg.InternalToken)
	httpReq.Header.Set("X-User-Id", strconv.FormatInt(user.ID, 10))

	resp, err := c.client.Do(httpReq)
	if err != nil {
		respondError(ctx, http.StatusBadGateway, "agent_unavailable",
			"Agent 服务不可用，请确认 agent 进程已启动")
		return
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode == http.StatusNotFound {
		respondError(ctx, http.StatusNotFound, "approval_not_found", "审批已失效（可能已超时或被处理）")
		return
	}
	if resp.StatusCode >= 400 {
		respondError(ctx, http.StatusBadGateway, "agent_error", "Agent 服务返回异常")
		return
	}

	ctx.JSON(http.StatusOK, gin.H{"ok": true, "approved": req.Approved})
}

// AnswerClarification 处理 POST /api/v1/agent/clarifications/:id/answer。
//
// 澄清闸门和审批闸门是同一个模式：Python 那边的运行**挂起**等这个回答，
// 回答一进来，同一个 run 会继续往下跑 —— 前端不用重新发 /agent/chat。
func (c *runtimeController) AnswerClarification(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	clarificationID := strings.TrimSpace(ctx.Param("id"))
	if clarificationID == "" {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "澄清 ID 不合法")
		return
	}

	var req struct {
		Text string `json:"text"`
	}
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}
	if strings.TrimSpace(req.Text) == "" {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "回答不能为空")
		return
	}

	endpoint := fmt.Sprintf(
		"%s/agent/clarifications/%s/answer", c.cfg.ServiceURL, clarificationID)
	// 用 json.Marshal 而不是拼字符串：回答里有引号或换行时
	// fmt.Sprintf 拼出来的 JSON 会直接坏掉。
	body, err := json.Marshal(map[string]string{"text": req.Text})
	if err != nil {
		respondError(ctx, http.StatusInternalServerError, "internal_error", "构造内部请求失败")
		return
	}

	httpReq, err := http.NewRequestWithContext(
		ctx.Request.Context(), http.MethodPost, endpoint, strings.NewReader(string(body)))
	if err != nil {
		respondError(ctx, http.StatusInternalServerError, "internal_error", "构造内部请求失败")
		return
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("X-Internal-Token", c.cfg.InternalToken)
	httpReq.Header.Set("X-User-Id", strconv.FormatInt(user.ID, 10))

	resp, err := c.client.Do(httpReq)
	if err != nil {
		respondError(ctx, http.StatusBadGateway, "agent_unavailable",
			"Agent 服务不可用，请确认 agent 进程已启动")
		return
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode == http.StatusNotFound {
		respondError(ctx, http.StatusNotFound, "clarification_not_found",
			"这次澄清已经失效（可能已超时）")
		return
	}
	if resp.StatusCode >= 400 {
		respondError(ctx, http.StatusBadGateway, "agent_error", "Agent 服务返回异常")
		return
	}

	ctx.JSON(http.StatusOK, gin.H{"ok": true})
}

// Health 转发健康检查，前端用它判断 Agent 是否在线。
func (c *runtimeController) Health(ctx *gin.Context) {
	httpReq, err := http.NewRequestWithContext(
		ctx.Request.Context(), http.MethodGet, c.cfg.ServiceURL+"/agent/health", nil)
	if err != nil {
		ctx.JSON(http.StatusOK, gin.H{"ok": false})
		return
	}
	httpReq.Header.Set("X-Internal-Token", c.cfg.InternalToken)
	httpReq.Header.Set("X-User-Id", "0")

	resp, err := c.client.Do(httpReq)
	if err != nil {
		ctx.JSON(http.StatusOK, gin.H{"ok": false, "reason": "agent_unreachable"})
		return
	}
	defer func() { _ = resp.Body.Close() }()

	body, _ := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	ctx.Data(http.StatusOK, "application/json; charset=utf-8", body)
}

// ---------------------------------------------------------------------------
// Trace 读取
// ---------------------------------------------------------------------------

// RunRow 是 trace 列表的一行。
type RunRow struct {
	RunID            string     `json:"run_id"`
	Title            string     `json:"title"`
	Intent           string     `json:"intent"`
	Confidence       float64    `json:"confidence"`
	Status           int        `json:"status"`
	Model            string     `json:"model"`
	PromptTokens     int        `json:"prompt_tokens"`
	CompletionTokens int        `json:"completion_tokens"`
	StartedAt        time.Time  `json:"started_at"`
	FinishedAt       *time.Time `json:"finished_at"`
	StepCount        int        `json:"step_count"`
}

// ListRuns 处理 GET /api/v1/agent/runs —— trace 面板的列表。
//
// 普通用户只能看到自己的运行；管理员可以看全部（观测需要）。
func (c *runtimeController) ListRuns(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	limit := 50
	if v := ctx.Query("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 200 {
			limit = n
		}
	}

	where := "r.user_id = ?"
	args := []any{user.ID}

	sql := `
		SELECT r.run_id, r.title, r.intent, r.confidence, r.status, r.model,
		       r.prompt_tokens, r.completion_tokens, r.started_at, r.finished_at,
		       (SELECT COUNT(*) FROM agent_steps s WHERE s.run_id = r.run_id) AS step_count
		  FROM agent_runs r
		 WHERE ` + where + `
		 ORDER BY r.started_at DESC
		 LIMIT ?`
	args = append(args, limit)

	var rows []RunRow
	if err := c.db.WithContext(ctx.Request.Context()).Raw(sql, args...).Scan(&rows).Error; err != nil {
		zap.L().Error("查询 agent 运行列表失败", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}
	if rows == nil {
		rows = []RunRow{}
	}
	ctx.JSON(http.StatusOK, gin.H{"items": rows, "count": len(rows)})
}

// StepRow 是 trace 详情的一步。
type StepRow struct {
	Seq        int    `json:"seq"`
	Kind       string `json:"kind"`
	Agent      string `json:"agent"`
	Name       string `json:"name"`
	Status     int    `json:"status"`
	InputJSON  string `json:"input_json"`
	OutputJSON string `json:"output_json"`
	Error      string `json:"error"`
	LatencyMS  *int   `json:"latency_ms"`
	CreatedAt  string `json:"created_at"`
}

// GetRun 处理 GET /api/v1/agent/runs/:runId —— 某次运行的完整轨迹。
func (c *runtimeController) GetRun(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	runID := strings.TrimSpace(ctx.Param("runId"))
	if runID == "" {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "run_id 不合法")
		return
	}

	var run RunRow
	err := c.db.WithContext(ctx.Request.Context()).Raw(`
		SELECT run_id, title, intent, confidence, status, model,
		       prompt_tokens, completion_tokens, started_at, finished_at
		  FROM agent_runs WHERE run_id = ?`, runID).Scan(&run).Error
	if err != nil {
		zap.L().Error("查询 agent 运行失败", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}
	if run.RunID == "" {
		respondError(ctx, http.StatusNotFound, "run_not_found", "运行记录不存在")
		return
	}

	// 越权访问统一返回 404，不泄露"这条运行存在但属于别人"。
	// 管理员也不例外：运行记录里的 title 就是用户原话，属于对话内容。
	if !c.belongsTo(ctx, runID, user.ID) {
		respondError(ctx, http.StatusNotFound, "run_not_found", "运行记录不存在")
		return
	}

	var steps []StepRow
	if err := c.db.WithContext(ctx.Request.Context()).Raw(`
		SELECT seq, kind, COALESCE(agent, '') AS agent, COALESCE(name, '') AS name,
		       status, COALESCE(input_json, '') AS input_json,
		       COALESCE(output_json, '') AS output_json,
		       COALESCE(error, '') AS error, latency_ms,
		       DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') AS created_at
		  FROM agent_steps WHERE run_id = ? ORDER BY seq`, runID).Scan(&steps).Error; err != nil {
		zap.L().Error("查询 agent 轨迹失败", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}
	if steps == nil {
		steps = []StepRow{}
	}

	ctx.JSON(http.StatusOK, gin.H{"run": run, "steps": steps})
}

// belongsTo 判断这条运行是否属于该用户。
func (c *runtimeController) belongsTo(ctx *gin.Context, runID string, userID int64) bool {
	var count int64
	if err := c.db.WithContext(ctx.Request.Context()).Raw(
		`SELECT COUNT(*) FROM agent_runs WHERE run_id = ? AND user_id = ?`, runID, userID).
		Scan(&count).Error; err != nil {
		return false
	}
	return count > 0
}

// ---------------------------------------------------------------------------
// 对话记录（读）
//
// 写入在 Python 侧（app/memory/conversation.py），这里只读。
// 与 trace 一样的取舍：写它的代码跟着产生它的进程走。
// ---------------------------------------------------------------------------

// ThreadRow 是会话列表的一行。
type ThreadRow struct {
	ThreadID      string     `json:"thread_id"`
	Title         string     `json:"title"`
	MessageCount  int        `json:"message_count"`
	LastMessageAt *time.Time `json:"last_message_at"`
	CreatedAt     time.Time  `json:"created_at"`
}

// MessageRow 是会话里的一条消息。
type MessageRow struct {
	ID        int64  `json:"id"`
	RunID     string `json:"run_id"`
	Role      string `json:"role"`
	Content   string `json:"content"`
	CardsJSON string `json:"cards_json"`
	CreatedAt string `json:"created_at"`
}

// ListThreads 处理 GET /api/v1/agent/threads。
func (c *runtimeController) ListThreads(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	var rows []ThreadRow
	if err := c.db.WithContext(ctx.Request.Context()).Raw(`
		SELECT thread_id, title, message_count, last_message_at, created_at
		  FROM agent_threads
		 WHERE user_id = ?
		 ORDER BY COALESCE(last_message_at, created_at) DESC
		 LIMIT 50`, user.ID).Scan(&rows).Error; err != nil {
		zap.L().Error("查询会话列表失败", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}
	if rows == nil {
		rows = []ThreadRow{}
	}
	ctx.JSON(http.StatusOK, gin.H{"items": rows, "count": len(rows)})
}

// ListMessages 处理 GET /api/v1/agent/threads/:threadId/messages。
//
// 越权访问统一返回 404，不泄露"这个会话存在但不属于你"。
func (c *runtimeController) ListMessages(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	threadID := strings.TrimSpace(ctx.Param("threadId"))
	if threadID == "" {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "thread_id 不合法")
		return
	}

	var ownerID int64
	_ = c.db.WithContext(ctx.Request.Context()).Raw(
		`SELECT user_id FROM agent_threads WHERE thread_id = ?`, threadID).Scan(&ownerID).Error
	// **会话内容严格属于账号本人。**
	// 这里刻意不给 admin 开后门：管理员能看"有多少次运行、成功率多少"
	// 这类聚合指标（/agent/metrics），但不能看别人聊了什么、查了什么。
	// 这既是产品要求，也是把"越权"这件事从根上堵死 ——
	// 只要有一个角色能跨账号读，就永远有泄漏面。
	if ownerID != user.ID {
		respondError(ctx, http.StatusNotFound, "thread_not_found", "会话不存在")
		return
	}

	var rows []MessageRow
	if err := c.db.WithContext(ctx.Request.Context()).Raw(`
		SELECT id, COALESCE(run_id, '') AS run_id, role, content,
		       COALESCE(cards_json, '') AS cards_json,
		       DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') AS created_at
		  FROM agent_messages
		 WHERE thread_id = ?
		 ORDER BY id`, threadID).Scan(&rows).Error; err != nil {
		zap.L().Error("查询会话消息失败", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}
	if rows == nil {
		rows = []MessageRow{}
	}
	ctx.JSON(http.StatusOK, gin.H{"thread_id": threadID, "items": rows, "count": len(rows)})
}

// DeleteThread 处理 DELETE /api/v1/agent/threads/:threadId。
func (c *runtimeController) DeleteThread(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	threadID := strings.TrimSpace(ctx.Param("threadId"))
	if threadID == "" {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "thread_id 不合法")
		return
	}

	// 事务里做三件事，缺一不可：
	//   1. 删会话（条件带 user_id，一条 SQL 同时完成鉴权与删除，避免"先查后删"的竞态）
	//   2. 删消息（agent_messages 没有外键级联，必须自己删）
	//   3. 删意图槽位（agent_intent_slots 同理，是路由层的跨轮状态）
	//
	// 为什么必须放在同一个事务：不然会出现"会话没了、消息/槽位还留着"的孤儿数据，
	// 越攒越多且没人能读到 —— 既浪费空间，也让"会话归属"的检查失去意义。
	notFound := false
	err := c.db.WithContext(ctx.Request.Context()).Transaction(func(tx *gorm.DB) error {
		res := tx.Exec(
			`DELETE FROM agent_threads WHERE thread_id = ? AND user_id = ?`, threadID, user.ID)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			// 不是你的会话，或者根本不存在 —— 都不改任何数据，让事务空提交
			notFound = true
			return nil
		}
		if err := tx.Exec(`DELETE FROM agent_messages WHERE thread_id = ?`, threadID).Error; err != nil {
			return err
		}
		return tx.Exec(`DELETE FROM agent_intent_slots WHERE thread_id = ?`, threadID).Error
	})
	if err != nil {
		zap.L().Error("删除会话失败", zap.String("thread_id", threadID), zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}
	if notFound {
		respondError(ctx, http.StatusNotFound, "thread_not_found", "会话不存在")
		return
	}

	ctx.Status(http.StatusNoContent)
}

// ---------------------------------------------------------------------------
// 评估指标
// ---------------------------------------------------------------------------

// MetricsVO 是评测层的聚合视图。
//
// "观测"看的是单次运行的细节（trace），"评估"看的是**一批运行的整体表现**：
// 成功率、澄清率、平均步数、直连与沙箱的比例。
// 这些数字才是判断"Agent 到底靠不靠谱"的依据。
type MetricsVO struct {
	WindowDays    int     `json:"window_days"`
	Runs          int     `json:"runs"`
	Succeeded     int     `json:"succeeded"`
	Failed        int     `json:"failed"`
	SuccessRate   float64 `json:"success_rate"`
	ClarifyRate   float64 `json:"clarify_rate"`
	AvgSteps      float64 `json:"avg_steps"`
	AvgTokens     float64 `json:"avg_tokens"`
	ToolCalls     int     `json:"tool_calls"`
	CodeRuns      int     `json:"code_runs"`
	DirectCalls   int     `json:"direct_calls"`
	WriteOps      int     `json:"write_ops"`
	Approvals     int     `json:"approvals"`
	ApprovedCount int     `json:"approved_count"`
}

// Metrics 处理 GET /api/v1/agent/metrics。
func (c *runtimeController) Metrics(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	days := 30
	if v := ctx.Query("days"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 365 {
			days = n
		}
	}

	scope := "user_id = ?"
	args := []any{user.ID}
	if user.HasRole("admin") && ctx.Query("all") == "1" {
		scope = "1 = 1"
		args = nil
	}

	out := MetricsVO{WindowDays: days}

	// 运行级指标
	var runStats struct {
		Runs      int     `gorm:"column:runs"`
		Succeeded int     `gorm:"column:succeeded"`
		Failed    int     `gorm:"column:failed"`
		Clarified int     `gorm:"column:clarified"`
		AvgTokens float64 `gorm:"column:avg_tokens"`
	}
	runSQL := `
		SELECT COUNT(*) AS runs,
		       COALESCE(SUM(status = 2), 0) AS succeeded,
		       COALESCE(SUM(status = 3), 0) AS failed,
		       COALESCE(SUM(confidence < 0.75), 0) AS clarified,
		       COALESCE(AVG(prompt_tokens + completion_tokens), 0) AS avg_tokens
		  FROM agent_runs
		 WHERE ` + scope + ` AND started_at > NOW() - INTERVAL ? DAY`
	runArgs := append(append([]any{}, args...), days)
	if err := c.db.WithContext(ctx.Request.Context()).Raw(runSQL, runArgs...).Scan(&runStats).Error; err != nil {
		zap.L().Error("统计运行指标失败", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}

	out.Runs = runStats.Runs
	out.Succeeded = runStats.Succeeded
	out.Failed = runStats.Failed
	out.AvgTokens = round1(runStats.AvgTokens)
	if out.Runs > 0 {
		out.SuccessRate = round1(float64(out.Succeeded) / float64(out.Runs) * 100)
		out.ClarifyRate = round1(float64(runStats.Clarified) / float64(out.Runs) * 100)
	}

	// 步骤级指标
	var stepStats []struct {
		Kind  string `gorm:"column:kind"`
		Count int    `gorm:"column:count"`
	}
	stepSQL := `
		SELECT s.kind, COUNT(*) AS count
		  FROM agent_steps s
		  JOIN agent_runs r ON r.run_id = s.run_id
		 WHERE ` + strings.ReplaceAll(scope, "user_id", "r.user_id") +
		` AND r.started_at > NOW() - INTERVAL ? DAY
		 GROUP BY s.kind`
	if err := c.db.WithContext(ctx.Request.Context()).Raw(stepSQL, runArgs...).Scan(&stepStats).Error; err == nil {
		totalSteps := 0
		for _, s := range stepStats {
			totalSteps += s.Count
			switch s.Kind {
			case "tool_call":
				out.ToolCalls = s.Count
			case "code_run":
				out.CodeRuns = s.Count
			case "approval_required":
				out.Approvals = s.Count
			case "approval_result":
				out.ApprovedCount = s.Count
			}
		}
		if out.Runs > 0 {
			out.AvgSteps = round1(float64(totalSteps) / float64(out.Runs))
		}
	}

	// 直连 vs 沙箱的比例：直接数 tool_call 事件里的 channel 不够（老数据没存），
	// 用 name 里是否出现 execute_code 来近似反而更稳。
	var directCount, codeCount, writeCount int
	_ = c.db.WithContext(ctx.Request.Context()).Raw(`
		SELECT
		  COALESCE(SUM(JSON_EXTRACT(input_json, '$.channel') = '"direct"'), 0) AS direct_count,
		  COALESCE(SUM(JSON_EXTRACT(input_json, '$.channel') = '"code"'), 0) AS code_count,
		  COALESCE(SUM(JSON_EXTRACT(input_json, '$.side_effect') = true), 0) AS write_count
		  FROM agent_steps s
		  JOIN agent_runs r ON r.run_id = s.run_id
		 WHERE `+strings.ReplaceAll(scope, "user_id", "r.user_id")+
		` AND s.kind = 'tool_call' AND r.started_at > NOW() - INTERVAL ? DAY`,
		runArgs...).Row().Scan(&directCount, &codeCount, &writeCount)
	out.DirectCalls = directCount
	out.WriteOps = writeCount

	ctx.JSON(http.StatusOK, out)
}

func round1(v float64) float64 {
	return math.Round(v*10) / 10
}
