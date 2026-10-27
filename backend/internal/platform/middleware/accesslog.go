package middleware

import (
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

// AccessLog 给每个请求打一条结构化日志。
//
// 字段刻意保持低基数（不含 URL 参数值、不含用户输入），
// 这样它还能被直接当作指标来源用。
//
// 分级策略：5xx 用 Error，4xx 用 Warn，其余 Info。
// 生产环境把 log.level 调到 info 以上，正常请求就不会刷屏。
func AccessLog(log *zap.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		path := c.Request.URL.Path
		query := c.Request.URL.RawQuery

		c.Next()

		status := c.Writer.Status()
		fields := []zap.Field{
			zap.String("request_id", RequestIDFrom(c)),
			zap.String("method", c.Request.Method),
			zap.String("path", path),
			zap.Int("status", status),
			zap.Int("size", c.Writer.Size()),
			zap.Duration("latency", time.Since(start)),
			zap.String("client_ip", c.ClientIP()),
		}
		if query != "" {
			fields = append(fields, zap.String("query", query))
		}
		if len(c.Errors) > 0 {
			fields = append(fields, zap.String("errors", c.Errors.String()))
		}

		switch {
		case status >= 500:
			log.Error("http_request", fields...)
		case status >= 400:
			log.Warn("http_request", fields...)
		default:
			log.Info("http_request", fields...)
		}
	}
}
