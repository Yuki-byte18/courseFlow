package agent

import (
	"errors"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/middleware"
	"courseflow/internal/platform/perms"
)

// apiKeyHeader 是前端传 DeepSeek Key 的请求头。
//
// 走请求头而不是请求体：它属于认证凭据，放头部语义更准确，
// 也更容易在网关层做「禁止记录该头」的配置。
const apiKeyHeader = "X-LLM-Api-Key"

// Controller 是对外暴露的 Agent 接口。
//
// 目前只有 Key 校验（顺带取回可用模型列表）。
// 对话、工具调用、编排都由 Agent 架构负责，尚未定型。
type Controller struct {
	verifier *Verifier
}

// NewController 构造 Controller。
func NewController(verifier *Verifier) *Controller {
	return &Controller{verifier: verifier}
}

// Register 注册 Agent 相关路由。
//
// 统一要求 agent:chat 权限：能校验 Key 的人，才是有权使用 Agent 的人。
func (c *Controller) Register(authed *gin.RouterGroup) {
	authed.POST("/agent/verify-key", middleware.RequirePerm(perms.AgentChat), c.VerifyKey)
}

// VerifyKey 处理 POST /api/v1/agent/verify-key。
//
// 校验 Key 并返回可用模型列表 —— 前端从这里拿模型做成下拉框，
// 所以模型名字永远不会写错。
func (c *Controller) VerifyKey(ctx *gin.Context) {
	user, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	apiKey := strings.TrimSpace(ctx.GetHeader(apiKeyHeader))

	result, err := c.verifier.VerifyKey(ctx.Request.Context(), apiKey)
	if err != nil {
		// 日志只记 user 与错误类别，**绝不记 Key**
		zap.L().Info("校验 API Key 未通过",
			zap.Int64("user_id", user.ID),
			zap.String("reason", err.Error()),
		)
		writeVerifyError(ctx, err)
		return
	}

	ctx.JSON(http.StatusOK, result)
}

// writeVerifyError 把校验错误映射成 HTTP 状态码。
//
// 状态码的选择有讲究 —— 前端据此决定提示语：
//   - 400 用户填错了格式 → 让他改
//   - 401 上游说 Key 无效 → 让他改
//   - 402 余额不足 → 提示去充值
//   - 403 Key 有效但没有可用的对话模型 → 提示换 Key
//   - 429 上游限流 → 提示稍后重试
//   - 502 我们连不上上游 → 提示检查网络，**不要怪用户的 Key**
func writeVerifyError(ctx *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrKeyMissing):
		respondError(ctx, http.StatusBadRequest, "missing_api_key", "请先填写 DeepSeek API Key")
	case errors.Is(err, ErrKeyFormat):
		respondError(ctx, http.StatusBadRequest, "invalid_api_key_format", "Key 格式不对，应以 sk- 开头")
	case errors.Is(err, ErrNoUsableModel):
		respondError(ctx, http.StatusForbidden, "no_usable_model", "这把 Key 没有可用的对话模型，请换一把或在 DeepSeek 控制台开通模型")
	case errors.Is(err, ErrUpstreamUnauthorized):
		respondError(ctx, http.StatusUnauthorized, "upstream_unauthorized", "Key 无效或没有权限")
	case errors.Is(err, ErrUpstreamBalance):
		respondError(ctx, http.StatusPaymentRequired, "upstream_insufficient_balance", "账户余额不足，请先到 DeepSeek 充值")
	case errors.Is(err, ErrUpstreamRateLimited):
		respondError(ctx, http.StatusTooManyRequests, "upstream_rate_limited", "请求过于频繁，请稍后再试")
	default:
		respondError(ctx, http.StatusBadGateway, "upstream_unreachable", err.Error())
	}
}

// respondError 写统一的错误结构，形状与其它业务域保持一致。
func respondError(ctx *gin.Context, status int, code, message string) {
	ctx.AbortWithStatusJSON(status, gin.H{
		"error": gin.H{
			"code":       code,
			"message":    message,
			"request_id": middleware.RequestIDFrom(ctx),
		},
	})
}
