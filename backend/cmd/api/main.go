// Command api 是在线层的 HTTP 入口。
//
// 装配顺序：config → logger → token → db → server。
// main 里只做装配，不写业务逻辑 —— package main 无法被其他包 import，
// 写进 main 的逻辑永远没法单元测试。
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"go.uber.org/zap"

	"courseflow/internal/platform/auth"
	"courseflow/internal/platform/config"
	"courseflow/internal/platform/logger"
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

	return server.New(cfg, log, db, tm).Run(ctx)
}
