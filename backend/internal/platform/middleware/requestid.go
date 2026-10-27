package middleware

import (
	"crypto/rand"
	"encoding/hex"

	"github.com/gin-gonic/gin"
)

// RequestIDHeader 是链路追踪用的请求头。
const RequestIDHeader = "X-Request-ID"

// ctxKeyRequestID 是 gin.Context 里存请求 ID 的 key。
const ctxKeyRequestID = "request_id"

// newID 生成 128 位随机 ID，避免为此引入 uuid 依赖。
func newID() string {
	var buf [16]byte
	if _, err := rand.Read(buf[:]); err != nil {
		return "0000000000000000"
	}
	return hex.EncodeToString(buf[:])
}

// RequestID 复用入站请求的 ID 或生成一个新的，并回写到响应头。
//
// 有了它，一次请求的日志、压测结果、用户报障才能串成一条线。
// 压测时也要带这个头，才能把压测流量和真实流量区分开。
func RequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader(RequestIDHeader)
		if id == "" {
			id = newID()
		}
		c.Set(ctxKeyRequestID, id)
		c.Header(RequestIDHeader, id)
		c.Next()
	}
}

// RequestIDFrom 从上下文取当前请求的 ID。
func RequestIDFrom(c *gin.Context) string {
	return c.GetString(ctxKeyRequestID)
}
