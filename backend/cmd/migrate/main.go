// Command migrate 是数据库迁移的统一入口。
//
// 为什么需要它：goose 要求把完整 DSN（含密码）放在命令行里，于是每个人都要
// 记一遍自己的连接串，而且它会被写进 shell 历史。这里改成从项目配置读 DSN，
// 团队成员只需要 `go run ./cmd/migrate -cmd up`。
//
// 用法：
//
//	go run ./cmd/migrate -cmd status     # 当前版本
//	go run ./cmd/migrate -cmd up         # 应用全部未执行的迁移
//	go run ./cmd/migrate -cmd down       # 回滚最后一步（危险）
//	go run ./cmd/migrate -cmd version    # 只看版本号
//	go run ./cmd/migrate -cmd create -name add_xxx   # 新建迁移文件
//
// 需要本机已安装 goose（https://github.com/pressly/goose）。
// 找不到时会打印安装命令，而不是静默失败。
package main

import (
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"courseflow/internal/platform/config"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "migrate 失败: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	command := flag.String("cmd", "status", "goose 子命令：status / up / down / version / create")
	dir := flag.String("dir", "migrations", "迁移文件目录")
	name := flag.String("name", "", "create 时的迁移名")
	goosePath := flag.String("goose", "", "goose 可执行文件路径（默认在 PATH 里找）")
	flag.Parse()

	gooseBin, err := resolveGoose(*goosePath)
	if err != nil {
		return err
	}

	cfg, err := config.Load("")
	if err != nil {
		return err
	}
	if cfg.SourceFile == "" {
		return fmt.Errorf("没找到配置文件（configs/config.yaml），无法确定要迁移哪个库")
	}

	args := []string{"-dir", *dir, "mysql", cfg.MySQL.DSN()}

	switch *command {
	case "create":
		if strings.TrimSpace(*name) == "" {
			return fmt.Errorf("create 需要 -name")
		}
		args = []string{"-dir", *dir, "create", *name, "sql"}
	default:
		args = append(args, *command)
	}

	// 只打印库名与主机，不打印 DSN —— 它会含密码
	fmt.Printf("库: %s@%s 目录: %s 子命令: %s\n\n",
		cfg.MySQL.Database, cfg.MySQL.Host, *dir, *command)

	cmd := exec.Command(gooseBin, args...)
	// 继承标准输入输出：既能实时看到 goose 的进度，也避免捕获管道带来的限制
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Dir = workingDir()

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("goose %s 执行失败: %w", *command, err)
	}
	return nil
}

// resolveGoose 找到 goose 可执行文件。
//
// 依次尝试：显式指定 → PATH → GOPATH/bin（go install 的默认落点）。
func resolveGoose(explicit string) (string, error) {
	if explicit != "" {
		return explicit, nil
	}

	if path, err := exec.LookPath("goose"); err == nil {
		return path, nil
	}

	if home, err := os.UserHomeDir(); err == nil {
		candidate := filepath.Join(home, "go", "bin", "goose.exe")
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
		candidate = filepath.Join(home, "go", "bin", "goose")
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
	}

	return "", fmt.Errorf(
		"找不到 goose。安装方式：go install github.com/pressly/goose/v3/cmd/goose@latest\n" +
			"或用 -goose 指定可执行文件路径",
	)
}

// workingDir 返回当前进程的工作目录。
//
// 单独抽出来是为了在目录获取失败时退化到 "." ——
// goose 的 -dir 是相对路径，工作目录错了会找不到迁移文件。
func workingDir() string {
	dir, err := os.Getwd()
	if err != nil {
		return "."
	}
	return dir
}
