// Package logger 构建进程级结构化日志。
package logger

import (
	"fmt"

	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

// New 构建 zap logger。
//
// 为什么一定开 Sampling：
// 压测时每请求一条日志，日志本身会先成为瓶颈（序列化 + 写盘），
// 你会看到 QPS 卡住却以为是 Redis 不行。采样让同一类日志每秒只保留
// 前 Initial 条，之后每 Thereafter 条留 1 条，高并发下自动降噪。
func New(env, level, format string) (*zap.Logger, error) {
	lvl, err := zapcore.ParseLevel(level)
	if err != nil {
		return nil, fmt.Errorf("解析日志级别 %q 失败: %w", level, err)
	}

	var cfg zap.Config
	if format == "json" {
		cfg = zap.NewProductionConfig()
		cfg.EncoderConfig.EncodeTime = zapcore.ISO8601TimeEncoder
	} else {
		cfg = zap.NewDevelopmentConfig()
		cfg.EncoderConfig.EncodeLevel = zapcore.CapitalColorLevelEncoder
	}

	cfg.Level = zap.NewAtomicLevelAt(lvl)
	cfg.Sampling = &zap.SamplingConfig{
		Initial:    100, // 每秒前 100 条全部保留
		Thereafter: 100, // 之后每 100 条保留 1 条
	}
	// 容器里统一走 stdout/stderr，由 docker logs / 采集器接管
	cfg.OutputPaths = []string{"stdout"}
	cfg.ErrorOutputPaths = []string{"stderr"}
	cfg.Development = env != "prod"

	log, err := cfg.Build()
	if err != nil {
		return nil, fmt.Errorf("构建 logger 失败: %w", err)
	}
	return log, nil
}