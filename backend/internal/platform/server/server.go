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

	"courseflow/internal/platform/config"
	"courseflow/internal/platform/middleware"
)

// Server 持有 gin engine 和底层的 http.Server。
type Server struct {
	cfg    *config.Config
	log    *zap.Logger
	db     *gorm.DB
	engine *gin.Engine
	http   *http.Server
}

// New 构建 Server 并注册中间件与路由。
func New(cfg *config.Config, log *zap.Logger, db *gorm.DB) *Server {
	if cfg.App.Env == "prod" {
		gin.SetMode(gin.ReleaseMode)
	} else {
		gin.SetMode(gin.DebugMode)
	}

	engine := gin.New()
	// Recovery 放最前面：任何后续中间件 panic 都能被兜住
	engine.Use(gin.Recovery(), middleware.RequestID(), middleware.AccessLog(log))

	s := &Server{cfg: cfg, log: log, db: db, engine: engine}
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
	v1.GET("/ping", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"message": "pong"})
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
