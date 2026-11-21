// Package agent 是业务 Agent 域的容器。
//
// 本文件只放「配置」——真正的 Agent 架构由你自己设计，这里刻意不做任何
// 编排、工具调度或提示词管理。
//
// 目前这个域里只有一件事：**校验用户自己的 DeepSeek API Key，
// 并取回这把 Key 实际能用的模型列表**。
//
// 为什么不硬编码模型 ID：模型名会随版本变化（v4 → v4.1 → …），
// 写死的列表一旦过期，用户会看到"模型不存在"却不知道正确名字是什么。
// 改成向 DeepSeek 的 /models 要真实列表，前端再从里面选 —— 永远不会错。
package agent

import (
	"os"
	"strings"
	"time"
)

const (
	// 只支持 DeepSeek。换供应商要改这里，也要改前端的引导文案。
	defaultBaseURL = "https://api.deepseek.com"

	// 默认模型：校验通过后优先选它，只要这把 Key 能用。
	// 如果它不在可用列表里，就退回到列表的第一个。
	defaultModel = "deepseek-v4.1-flash"

	// 校验请求的超时。用户点「测试并保存」时不该等太久。
	defaultTimeout = 15 * time.Second
)

// Config 是 Agent 域的运行参数。
//
// 刻意只从环境变量读，不改动 platform/config：
// 这一块还在设计阶段，过早固化到 config.yaml 会让后续调整反复改配置文件。
// 等 Agent 架构定下来，再统一收进 Viper。
type Config struct {
	BaseURL string
	// Model 是**偏好**模型，不是唯一模型。
	// 校验通过后优先选它；不可用时由可用列表里的第一个兜底。
	Model   string
	Timeout time.Duration
}

// DefaultConfig 从环境变量读取配置，缺省值可用。
//
//	AGENT_LLM_BASE_URL  默认 https://api.deepseek.com
//	AGENT_LLM_MODEL     默认 deepseek-v4.1-flash（偏好模型）
func DefaultConfig() Config {
	cfg := Config{
		BaseURL: defaultBaseURL,
		Model:   defaultModel,
		Timeout: defaultTimeout,
	}

	if v := strings.TrimSpace(os.Getenv("AGENT_LLM_BASE_URL")); v != "" {
		cfg.BaseURL = strings.TrimRight(v, "/")
	}
	if v := strings.TrimSpace(os.Getenv("AGENT_LLM_MODEL")); v != "" {
		cfg.Model = v
	}

	return cfg
}

// isChatModel 判断一个模型 ID 是不是对话模型。
//
// DeepSeek 的 /models 也会返回向量化等非对话模型，
// 把它们列进下拉框只会让用户选到就跑不通的选项。
// 这是个保守的黑名单式过滤：宁可多留，也不误杀对话模型。
func isChatModel(id string) bool {
	lower := strings.ToLower(id)
	for _, bad := range []string{"embed", "rerank", "image", "tts", "asr", "moderation"} {
		if strings.Contains(lower, bad) {
			return false
		}
	}
	return true
}
