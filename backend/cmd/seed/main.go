// Command seed 创建首个管理员账号。
//
// 为什么需要它：注册接口只创建学生，而教师/管理员必须由管理员创建——
// 这就形成了「没有管理员就造不出管理员」的死锁。seed 打破这个循环，
// 并且保证可复现（而不是手工往数据库里 INSERT 一行）。
//
// 幂等：如果该用户已存在且是管理员，直接跳过并正常退出。
// 安全：如果该用户名已被一个【非管理员】占用，直接报错退出，绝不自动提权
// （否则别人先注册了 admin 就能借 seed 拿到管理员）。
package main

import (
	"context"
	"fmt"
	"os"
	"strings"

	"go.uber.org/zap"

	"courseflow/internal/identity"
	"courseflow/internal/platform/auth"
	"courseflow/internal/platform/config"
	"courseflow/internal/platform/logger"
	"courseflow/internal/platform/mysql"
)

// 开发环境默认值。可用环境变量覆盖：
//
//	SEED_ADMIN_USERNAME / SEED_ADMIN_PASSWORD / SEED_ADMIN_REALNAME
const (
	defaultUsername = "admin"
	defaultPassword = "123456"
	defaultRealName = "教务管理员"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "seed 失败: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	username := envOr("SEED_ADMIN_USERNAME", defaultUsername)
	password := envOr("SEED_ADMIN_PASSWORD", defaultPassword)
	realName := envOr("SEED_ADMIN_REALNAME", defaultRealName)

	cfg, err := config.Load("")
	if err != nil {
		return err
	}

	log, err := logger.New(cfg.App.Env, cfg.Log.Level, cfg.Log.Format)
	if err != nil {
		return err
	}
	defer func() { _ = log.Sync() }()

	if password == defaultPassword {
		log.Warn("正在使用内置的默认管理员密码，仅限本地开发！",
			zap.String("hint", "用环境变量 SEED_ADMIN_PASSWORD 覆盖，或首次登录后立即修改"),
		)
	}

	tm, err := auth.NewTokenManager(cfg.JWT)
	if err != nil {
		return err
	}

	ctx := context.Background()

	db, err := mysql.New(ctx, cfg.MySQL, log)
	if err != nil {
		return err
	}

	svc := identity.NewService(db, tm, log)

	// 1. 该用户名是否已被占用
	existing, err := svc.FindUserSnapshot(ctx, username)
	if err != nil {
		return err
	}

	if existing != nil {
		if existing.HasAdmin {
			log.Info("管理员账号已存在，无需重复创建",
				zap.String("username", username),
				zap.Int64("user_id", existing.ID),
			)
			return nil
		}
		// 用户名被非管理员占了：拒绝提权，让人去改配置
		return fmt.Errorf(
			"用户名 %q 已被一个非管理员账号占用（user_id=%d）。"+
				"请改用 SEED_ADMIN_USERNAME 指定其它用户名，或先手工清理该账号",
			username, existing.ID,
		)
	}

	// 2. 创建管理员
	info, err := svc.CreateUser(ctx, identity.CreateUserInput{
		Username: username,
		Password: password,
		RealName: realName,
		Roles:    []string{identity.RoleAdmin},
	})
	if err != nil {
		return err
	}

	log.Info("管理员账号创建成功",
		zap.Int64("user_id", info.ID),
		zap.String("username", info.Username),
		zap.Strings("roles", info.Roles),
		zap.Int("perm_count", len(info.Perms)),
	)

	if len(info.Perms) == 0 {
		log.Warn("该管理员当前没有任何权限，请确认已执行 0002_permissions_seed.sql")
	}

	return nil
}

func envOr(key, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return fallback
}
