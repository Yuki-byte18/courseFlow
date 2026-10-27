// Command api 是在线层的 HTTP 入口。
//
// 装配顺序：config → logger → token → db → 各域 service/controller → server。
// main 里只做装配，不写业务逻辑 —— package main 无法被其他包 import，
// 写进 main 的逻辑永远没法单元测试。
//
// 路由由这里统一挂载：platform/server 不 import 任何业务域，
// 因此「哪个域暴露哪些路由」这件事必须在装配层完成。
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"go.uber.org/zap"

	"courseflow/internal/identity"
	"courseflow/internal/platform/auth"
	"courseflow/internal/platform/config"
	"courseflow/internal/platform/logger"
	"courseflow/internal/platform/middleware"
	"courseflow/internal/platform/mysql"
	"courseflow/internal/platform/server"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "courseflow api 启动失败: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	configPath := flag.String("config", "", "配置文件路径（默认查找 ./configs/config.yaml）")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		return err
	}

	log, err := logger.New(cfg.App.Env, cfg.Log.Level, cfg.Log.Format)
	if err != nil {
		return err
	}
	defer func() { _ = log.Sync() }()

	// 配置文件找不到时会静默回退到内置默认值。显式提示出来，
	// 否则「密码为什么是空的」这类问题极难定位（MySQL 只会报 using password: NO）。
	if cfg.SourceFile == "" {
		log.Warn("未找到配置文件，正在使用内置默认值 + 环境变量",
			zap.String("searched", "./configs/config.yaml"),
			zap.String("hint", "请从 config.example.yaml 复制一份 config.yaml 并填入真实值"),
		)
	} else {
		log.Info("配置已加载", zap.String("file", cfg.SourceFile))
	}

	tm, err := auth.NewTokenManager(cfg.JWT)
	if err != nil {
		return err
	}

	log.Info("启动 courseflow api",
		zap.String("service", cfg.App.Name),
		zap.String("env", cfg.App.Env),
		zap.String("addr", cfg.Server.Addr()),
		zap.Duration("jwt_ttl", tm.TTL()),
	)

	// SIGTERM 是容器/虚拟机停服务的信号；SIGINT 是 Ctrl+C
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	db, err := mysql.New(ctx, cfg.MySQL, log)
	if err != nil {
		return err
	}

	srv := server.New(cfg, log, db, tm)

	// ---- 挂载业务域路由 ----
	// 公开路由挂在 srv.API()，需登录的挂在 srv.Authed()。
	// RequireRole / RequirePerm 必须叠在 RequireAuth 之后，否则上下文里没有用户。
	identityCtl := identity.NewController(identity.NewService(db, tm, log))
	identityCtl.RegisterPublic(srv.API())
	identityCtl.RegisterProtected(srv.Authed())

	adminGroup := srv.Authed().Group("/admin", middleware.RequireRole(identity.RoleAdmin))
	identityCtl.RegisterAdmin(adminGroup)

	// 后续域照着这个模式接：
	//   studentGroup := srv.Authed().Group("/student", middleware.RequireRole(identity.RoleStudent))
	//   enrollmentCtl.RegisterStudent(studentGroup)
	//   teacherGroup := srv.Authed().Group("/teacher", middleware.RequireRole(identity.RoleTeacher))
	//   rosterCtl.RegisterTeacher(teacherGroup)

	return srv.Run(ctx)
}
