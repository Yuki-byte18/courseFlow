// Package server 装配 HTTP 层：中间件、路由、生命周期。
package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
	"gorm.io/gorm"

	"courseflow/internal/platform/auth"
	"courseflow/internal/platform/config"
	"courseflow/internal/platform/middleware"
)

// Server 持有 gin engine 和底层的 http.Server。
type Server struct {
	cfg    *config.Config
	log    *zap.Logger
	db     *gorm.DB
	tm     *auth.TokenManager
	engine *gin.Engine
	http   *http.Server
}

// New 构建 Server 并注册中间件与路由。
func New(cfg *config.Config, log *zap.Logger, db *gorm.DB, tm *auth.TokenManager) *Server {
	if cfg.App.Env == "prod" {
		gin.SetMode(gin.ReleaseMode)
	} else {
		gin.SetMode(gin.DebugMode)
	}

	engine := gin.New()
	// Recovery 放最前面：任何后续中间件 panic 都能被兜住
	engine.Use(gin.Recovery(), middleware.RequestID(), middleware.AccessLog(log))

	s := &Server{cfg: cfg, log: log, db: db, tm: tm, engine: engine}
	s.registerRoutes()

	s.http = &http.Server{
		Addr:    cfg.Server.Addr(),
		Handler: engine,
		// ReadHeaderTimeout 挡慢速攻击；后两个限制单个请求的总时长
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       cfg.Server.ReadTimeout,
		WriteTimeout:      cfg.Server.WriteTimeout,
		IdleTimeout:       90 * time.Second,
	}
	return s
}

// Engine 暴露 gin engine，方便测试用 httptest 直接打 handler，不必占端口。
func (s *Server) Engine() *gin.Engine { return s.engine }

func (s *Server) registerRoutes() {
	// 存活探针：绝不碰任何依赖。
	// 如果这里查了数据库，数据库抖一下就会让健康进程被重启，反而放大故障。
	s.engine.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{
			"status":  "ok",
			"service": s.cfg.App.Name,
			"env":     s.cfg.App.Env,
		})
	})

	// 就绪探针：依赖不可用时返回 503，让流量不要进来
	s.engine.GET("/readyz", s.handleReadyz)

	v1 := s.engine.Group("/api/v1")

	// ---- 公开路由：不需要登录 ----
	v1.GET("/ping", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"message": "pong"})
	})

	// ---- 需要登录 ----
	authed := v1.Group("", middleware.RequireAuth(s.tm, s.log))
	authed.GET("/me", s.handleMe)

	// ---- 三端路由组 ----
	// 目前还没有业务接口，先留结构。接口实现后按下面这样挂：
	//
	//   student := authed.Group("/student", middleware.RequireRole("student"))
	//   teacher := authed.Group("/teacher", middleware.RequireRole("teacher"))
	//   admin   := authed.Group("/admin",   middleware.RequireRole("admin"))
	//
	// 粒度更细的用权限码，例如排课接口：
	//   admin.POST("/offerings", middleware.RequirePerm("offering:write"), h.CreateOffering)
	//
	// 注意：RequireRole / RequirePerm 必须挂在 RequireAuth 之后，
	// 否则上下文里没有用户，会一律返回 401。
}

// handleMe 返回当前登录用户（信息来自 JWT，不查库）。
// 前端用它决定渲染哪些菜单和按钮。
func (s *Server) handleMe(c *gin.Context) {
	u, ok := middleware.CurrentUser(c)
	if !ok {
		c.JSON(http.StatusUnauthorized, gin.H{
			"error": gin.H{
				"code":       "unauthorized",
				"message":    "认证失败，请重新登录",
				"request_id": middleware.RequestIDFrom(c),
			},
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"user_id":  u.ID,
		"username": u.Username,
		"roles":    u.Roles,
		"perms":    u.Perms,
	})
}

func (s *Server) handleReadyz(c *gin.Context) {
	checks := gin.H{}
	ready := true

	if s.db != nil {
		if err := s.pingDB(c.Request.Context()); err != nil {
			ready = false
			checks["mysql"] = err.Error()
		} else {
			checks["mysql"] = "ok"
		}
	}

	status := http.StatusOK
	state := "ready"
	if !ready {
		status = http.StatusServiceUnavailable
		state = "not_ready"
	}
	c.JSON(status, gin.H{"status": state, "checks": checks})
}

func (s *Server) pingDB(ctx context.Context) error {
	sqlDB, err := s.db.DB()
	if err != nil {
		return fmt.Errorf("获取 sql.DB 失败: %w", err)
	}
	pingCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	return sqlDB.PingContext(pingCtx)
}

// Run 启动服务并阻塞，直到 ctx 被取消后优雅退出。
func (s *Server) Run(ctx context.Context) error {
	errCh := make(chan error, 1)
	go func() {
		s.log.Info("http server 已启动", zap.String("addr", s.http.Addr))
		if err := s.http.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
	}

	s.log.Info("收到退出信号，开始排空在途请求")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), s.cfg.Server.ShutdownTimeout)
	defer cancel()

	if err := s.http.Shutdown(shutdownCtx); err != nil {
		return fmt.Errorf("优雅退出失败: %w", err)
	}
	s.log.Info("http server 已停止")
	return nil
}
