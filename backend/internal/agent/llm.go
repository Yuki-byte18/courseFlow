package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"go.uber.org/zap"
)

// Key 校验相关的错误。
//
// 分成「用户填错」和「上游出问题」两类，
// 前端据此决定是让用户改 Key，还是提示稍后再试。
var (
	ErrKeyMissing           = errors.New("缺少 API Key")
	ErrKeyFormat            = errors.New("Key 格式不对，应以 sk- 开头")
	ErrNoUsableModel        = errors.New("这把 Key 没有可用的对话模型")
	ErrUpstreamUnauthorized = errors.New("Key 无效或没有权限")
	ErrUpstreamBalance      = errors.New("账户余额不足，请先充值")
	ErrUpstreamRateLimited  = errors.New("请求过于频繁，请稍后再试")
	ErrUpstreamUnreachable  = errors.New("无法连接 DeepSeek，请检查网络")
)

// VerifyResult 是一次 Key 校验的结果。
type VerifyResult struct {
	OK bool `json:"ok"`
	// Models 是这把 Key 实际能用的对话模型，前端从里面选
	Models []string `json:"models"`
	// Default 是推荐优先使用的模型（服务端偏好模型，不可用时取列表第一个）
	Default string `json:"default"`
}

// Verifier 负责校验用户自备的 DeepSeek API Key。
//
// 它**不缓存、不持久化、不记录** Key：每次校验拿到即用，用完即弃。
// 这是 BYOK（用户自备密钥）的核心约束，也是我们不托管密钥的前提。
type Verifier struct {
	cfg    Config
	client *http.Client
	log    *zap.Logger
}

// NewVerifier 构造 Verifier。
func NewVerifier(cfg Config, log *zap.Logger) *Verifier {
	return &Verifier{
		cfg:    cfg,
		client: &http.Client{Timeout: cfg.Timeout},
		log:    log,
	}
}

// modelsResponse 是 OpenAI 兼容的 /models 响应。
type modelsResponse struct {
	Data []struct {
		ID string `json:"id"`
	} `json:"data"`
}

// VerifyKey 校验一把 Key，并返回它可用的对话模型列表。
//
// 用 GET /models 而不是发一次对话：前者不消耗 token，
// 而且能顺带拿到真实可用的模型 —— 这正是"不硬编码模型名"的关键。
//
// **绝不把 key 写进任何日志或错误信息**——错误信息会原样返回给前端，
// 日志会被采集，两处都不能出现密钥。
func (v *Verifier) VerifyKey(ctx context.Context, apiKey string) (*VerifyResult, error) {
	apiKey = strings.TrimSpace(apiKey)
	if apiKey == "" {
		return nil, ErrKeyMissing
	}
	if !strings.HasPrefix(apiKey, "sk-") || len(apiKey) < 16 {
		return nil, ErrKeyFormat
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, v.cfg.BaseURL+"/models", nil)
	if err != nil {
		return nil, fmt.Errorf("构造校验请求失败: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)
	req.Header.Set("Accept", "application/json")

	resp, err := v.client.Do(req)
	if err != nil {
		// 网络层错误：不把 err 原样返回，里面可能带完整请求信息
		v.log.Warn("校验 Key 失败：无法连接上游", zap.Error(err))
		return nil, ErrUpstreamUnreachable
	}
	defer func() { _ = resp.Body.Close() }()

	body, _ := io.ReadAll(io.LimitReader(resp.Body, 64*1024))

	switch {
	case resp.StatusCode == http.StatusOK:
		// 正常，继续解析
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden:
		return nil, ErrUpstreamUnauthorized
	case resp.StatusCode == http.StatusPaymentRequired:
		return nil, ErrUpstreamBalance
	case resp.StatusCode == http.StatusTooManyRequests:
		return nil, ErrUpstreamRateLimited
	default:
		v.log.Warn("校验 Key 失败：上游返回异常状态",
			zap.Int("status", resp.StatusCode),
			zap.String("body_head", head(body, 200)),
		)
		return nil, fmt.Errorf("%w（HTTP %d）", ErrUpstreamUnreachable, resp.StatusCode)
	}

	var parsed modelsResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		// 200 但响应体不是预期结构：上游可能改版了
		v.log.Warn("校验 Key 失败：无法解析 /models 响应", zap.String("body_head", head(body, 200)))
		return nil, fmt.Errorf("%w（响应格式无法解析）", ErrUpstreamUnreachable)
	}

	models := make([]string, 0, len(parsed.Data))
	defaultModel := ""
	for _, item := range parsed.Data {
		id := strings.TrimSpace(item.ID)
		if id == "" || !isChatModel(id) {
			continue
		}
		models = append(models, id)
		// 偏好模型出现了就记下来，最后用它当默认值
		if id == v.cfg.Model {
			defaultModel = id
		}
	}

	if len(models) == 0 {
		v.log.Warn("校验 Key 通过，但没有可用的对话模型")
		return nil, ErrNoUsableModel
	}

	if defaultModel == "" {
		// 偏好模型不在这把 Key 的可用范围里 —— 退回第一个，而不是报错。
		// 用户能看到完整列表，自己选就行。
		defaultModel = models[0]
	}

	v.log.Info("API Key 校验通过",
		zap.Int("model_count", len(models)),
		zap.String("default", defaultModel),
	)

	return &VerifyResult{OK: true, Models: models, Default: defaultModel}, nil
}

// head 截断响应体，用于日志。避免上游返回超长内容把日志刷爆。
func head(b []byte, n int) string {
	if len(b) <= n {
		return string(b)
	}
	return string(b[:n]) + "…"
}
