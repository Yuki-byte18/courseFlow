// Package mysql 提供 GORM 连接池与事务辅助。
package mysql

import (
	"context"
	"fmt"
	"time"

	"go.uber.org/zap"
	gormmysql "gorm.io/driver/mysql"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"

	"courseflow/internal/platform/config"
)

// New 建立连接池并做一次探活。
//
// 连接池参数从配置读而不是写死在代码里：压测时要反复调它们，
// 硬编码意味着每次调参都得改代码重新编译。
func New(ctx context.Context, cfg config.MySQLConfig, log *zap.Logger) (*gorm.DB, error) {
	db, err := gorm.Open(gormmysql.Open(cfg.DSN()), &gorm.Config{
		Logger: newGormLogger(cfg, log),
		// 关掉默认事务：GORM 默认给每个写操作套一层事务，
		// 对批量落库这种高频写入是纯额外开销。需要事务时用 WithTx 显式开。
		SkipDefaultTransaction: true,
		// PrepareStmt 会缓存预编译语句，但高并发下占用较多连接内存，
		// 先关掉，等压测确认它是瓶颈再打开对比。
		PrepareStmt: false,
		NowFunc:     func() time.Time { return time.Now().Local() },
	})
	if err != nil {
		return nil, fmt.Errorf("连接 MySQL 失败: %w", err)
	}

	sqlDB, err := db.DB()
	if err != nil {
		return nil, fmt.Errorf("获取底层 sql.DB 失败: %w", err)
	}

	sqlDB.SetMaxOpenConns(cfg.MaxOpenConns)
	sqlDB.SetMaxIdleConns(cfg.MaxIdleConns)
	sqlDB.SetConnMaxLifetime(cfg.ConnMaxLifetime)
	sqlDB.SetConnMaxIdleTime(cfg.ConnMaxIdleTime)

	// 启动时探活一次：连不上就直接失败，别让服务带着坏连接跑起来
	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := sqlDB.PingContext(pingCtx); err != nil {
		return nil, fmt.Errorf(
			"MySQL 探活失败（检查 host/port/账号密码，以及库是否已建）: %w", err)
	}

	log.Info("mysql 连接成功",
		zap.String("host", cfg.Host),
		zap.Int("port", cfg.Port),
		zap.String("database", cfg.Database),
		zap.Int("max_open_conns", cfg.MaxOpenConns),
		zap.Int("max_idle_conns", cfg.MaxIdleConns),
	)
	return db, nil
}

// WithTx 在事务中执行 fn，fn 返回错误则回滚。
func WithTx(ctx context.Context, db *gorm.DB, fn func(tx *gorm.DB) error) error {
	return db.WithContext(ctx).Transaction(fn)
}

// zapWriter 把 GORM 的日志转给 zap，避免出现两套日志格式。
type zapWriter struct{ l *zap.SugaredLogger }

func (w zapWriter) Printf(format string, args ...any) {
	w.l.Debugf(format, args...)
}

func newGormLogger(cfg config.MySQLConfig, log *zap.Logger) gormlogger.Interface {
	lvl := gormlogger.Warn
	switch cfg.LogLevel {
	case "silent":
		lvl = gormlogger.Silent
	case "error":
		lvl = gormlogger.Error
	case "info":
		lvl = gormlogger.Info
	}

	return gormlogger.New(zapWriter{l: log.Sugar()}, gormlogger.Config{
		SlowThreshold:             cfg.SlowThreshold,
		LogLevel:                  lvl,
		IgnoreRecordNotFoundError: true, // 查不到记录是业务常态，不该刷日志
		Colorful:                  false,
	})
}
