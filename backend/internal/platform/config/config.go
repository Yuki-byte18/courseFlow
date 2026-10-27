// Package config 负责加载、校验并暴露应用配置。
//
// 优先级：环境变量 > 配置文件 > 内置默认值。
//
// 每个配置项都在 defaults() 里给了默认值，这不是偷懒：
// viper 的 AutomaticEnv 只对「已知的 key」生效，没有默认值的嵌套 key
// 取不到环境变量的值。所以新增配置项时，defaults() 和结构体必须同时改。
package config

import (
	"errors"
	"fmt"
	"net"
	"os"
	"strings"
	"time"

	"github.com/spf13/viper"
)

const envPrefix = "COURSEFLOW"

// defaultJWTSecret 只用于开发环境。生产环境必须覆盖它，Validate 会拦。
const defaultJWTSecret = "dev-only-insecure-secret-change-me"

// Config 是配置树的根。
type Config struct {
	// SourceFile 是实际读到的配置文件路径，为空表示没找到文件、
	// 用的是内置默认值 + 环境变量。它不来自配置文件，由 Load 填充。
	SourceFile string `mapstructure:"-"`

	App    AppConfig    `mapstructure:"app"`
	Server ServerConfig `mapstructure:"server"`
	Log    LogConfig    `mapstructure:"log"`
	MySQL  MySQLConfig  `mapstructure:"mysql"`
	JWT    JWTConfig    `mapstructure:"jwt"`
}

// AppConfig 是应用级配置。
type AppConfig struct {
	Name string `mapstructure:"name"`
	Env  string `mapstructure:"env"` // dev | prod
}

// ServerConfig 是 HTTP 服务配置。
type ServerConfig struct {
	Host            string        `mapstructure:"host"`
	Port            int           `mapstructure:"port"`
	ReadTimeout     time.Duration `mapstructure:"read_timeout"`
	WriteTimeout    time.Duration `mapstructure:"write_timeout"`
	ShutdownTimeout time.Duration `mapstructure:"shutdown_timeout"`
}

// Addr 返回 host:port 形式的监听地址。
func (s ServerConfig) Addr() string {
	return net.JoinHostPort(s.Host, fmt.Sprintf("%d", s.Port))
}

// LogConfig 是日志配置。
type LogConfig struct {
	Level  string `mapstructure:"level"`  // debug | info | warn | error
	Format string `mapstructure:"format"` // console | json
}

// MySQLConfig 是数据库连接与连接池配置。
type MySQLConfig struct {
	Host            string        `mapstructure:"host"`
	Port            int           `mapstructure:"port"`
	User            string        `mapstructure:"user"`
	Password        string        `mapstructure:"password"`
	Database        string        `mapstructure:"database"`
	Charset         string        `mapstructure:"charset"`
	MaxOpenConns    int           `mapstructure:"max_open_conns"`
	MaxIdleConns    int           `mapstructure:"max_idle_conns"`
	ConnMaxLifetime time.Duration `mapstructure:"conn_max_lifetime"`
	ConnMaxIdleTime time.Duration `mapstructure:"conn_max_idle_time"`
	SlowThreshold   time.Duration `mapstructure:"slow_threshold"`
	LogLevel        string        `mapstructure:"log_level"`
}

// DSN 返回 go-sql-driver/mysql 的连接串。
func (m MySQLConfig) DSN() string {
	return fmt.Sprintf(
		"%s:%s@tcp(%s)/%s?charset=%s&parseTime=true&loc=Local&timeout=5s&readTimeout=5s&writeTimeout=5s",
		m.User, m.Password,
		net.JoinHostPort(m.Host, fmt.Sprintf("%d", m.Port)),
		m.Database, m.Charset,
	)
}

// JWTConfig 是鉴权配置。
type JWTConfig struct {
	Secret string        `mapstructure:"secret"` // HS256 对称密钥，至少 16 字符
	Issuer string        `mapstructure:"issuer"` // 签发者，校验时会比对
	Expire time.Duration `mapstructure:"expire"` // token 有效期
}

// defaults 与上面的结构体标签一一对应，改一处必须改另一处。
func defaults() map[string]any {
	return map[string]any{
		"app.name": "courseflow",
		"app.env":  "dev",

		"server.host":             "0.0.0.0",
		"server.port":             8080,
		"server.read_timeout":     "10s",
		"server.write_timeout":    "15s",
		"server.shutdown_timeout": "10s",

		"log.level":  "debug",
		"log.format": "console",

		"mysql.host":               "127.0.0.1",
		"mysql.port":               3306,
		"mysql.user":               "root",
		"mysql.password":           "",
		"mysql.database":           "courseflow",
		"mysql.charset":            "utf8mb4",
		"mysql.max_open_conns":     200,
		"mysql.max_idle_conns":     50,
		"mysql.conn_max_lifetime":  "30m",
		"mysql.conn_max_idle_time": "10m",
		"mysql.slow_threshold":     "200ms",
		"mysql.log_level":          "warn",

		"jwt.secret": defaultJWTSecret,
		"jwt.issuer": "courseflow",
		"jwt.expire": "2h",
	}
}

// Load 从 path 读取配置。path 为空时依次查找 ./configs/config.yaml 和 .。
//
// 找不到配置文件不算错误：默认值 + 环境变量本身就是一份合法配置，
// 这让容器里只靠环境变量部署成为可能。但 Load 会把「用了哪个文件」记在
// Config.SourceFile 里，调用方应当据此提示，否则「密码为什么是空的」
// 这类问题会非常难查。
func Load(path string) (*Config, error) {
	v := viper.New()
	for key, val := range defaults() {
		v.SetDefault(key, val)
	}

	v.SetEnvPrefix(envPrefix)
	v.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))
	v.AutomaticEnv()

	if path != "" {
		v.SetConfigFile(path)
	} else {
		v.SetConfigName("config")
		v.SetConfigType("yaml")
		v.AddConfigPath("./configs")
		v.AddConfigPath(".")
	}

	if err := v.ReadInConfig(); err != nil {
		var notFound viper.ConfigFileNotFoundError
		var pathErr *os.PathError
		if !errors.As(err, &notFound) && !errors.As(err, &pathErr) {
			return nil, fmt.Errorf("读取配置文件 %q 失败: %w", path, err)
		}
	}

	var cfg Config
	if err := v.Unmarshal(&cfg); err != nil {
		return nil, fmt.Errorf("解析配置失败: %w", err)
	}
	cfg.SourceFile = v.ConfigFileUsed()

	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	return &cfg, nil
}

// Validate 在启动时就把「运行期才会炸」的配置问题拦下来。
func (c *Config) Validate() error {
	if c.Server.Port <= 0 || c.Server.Port > 65535 {
		return fmt.Errorf("配置错误: server.port %d 不在 1-65535 范围内", c.Server.Port)
	}
	if c.MySQL.Host == "" || c.MySQL.User == "" || c.MySQL.Database == "" {
		return errors.New("配置错误: mysql.host / mysql.user / mysql.database 不能为空")
	}
	if c.MySQL.MaxOpenConns < c.MySQL.MaxIdleConns {
		return fmt.Errorf(
			"配置错误: mysql.max_open_conns(%d) 不能小于 mysql.max_idle_conns(%d)",
			c.MySQL.MaxOpenConns, c.MySQL.MaxIdleConns,
		)
	}
	if len(c.JWT.Secret) < 16 {
		return fmt.Errorf("配置错误: jwt.secret 至少需要 16 个字符（当前 %d）", len(c.JWT.Secret))
	}
	if c.App.Env == "prod" && c.JWT.Secret == defaultJWTSecret {
		return errors.New("配置错误: 生产环境必须覆盖 jwt.secret，不能沿用内置的开发默认值")
	}
	if c.JWT.Expire <= 0 {
		return errors.New("配置错误: jwt.expire 必须大于 0")
	}
	return nil
}
