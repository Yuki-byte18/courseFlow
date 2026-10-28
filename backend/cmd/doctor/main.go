// Command doctor 检查运行时数据是否与代码期望一致。
//
// 起因：教师端「一键导入课表」返回 403 缺失 offering:import 权限，
// 但迁移里明明给 teacher 角色授过权。这类问题可能出在四五层上
// （迁移没跑 / 跑在另一个库 / 角色没绑 / token 是旧的），
// 光看代码和日志分不清，不如直接查库。
//
// 用法：
//
//	go run ./cmd/doctor                       # 只看全局状态
//	go run ./cmd/doctor -user seed_teacher_1  # 再看某个账号登录后会拿到什么权限
package main

import (
	"context"
	"flag"
	"fmt"
	"os"

	"go.uber.org/zap"
	"gorm.io/gorm"

	"courseflow/internal/platform/config"
	"courseflow/internal/platform/mysql"
)

/** 关注的权限点 */
const targetPerm = "offering:import"

func main() {
	username := flag.String("user", "", "额外检查某个账号的角色与权限")
	listCourses := flag.Int("courses", 0, "列出前 N 个课程的课程号（导入课表时要用）")
	flag.Parse()

	if err := run(*username, *listCourses); err != nil {
		fmt.Fprintf(os.Stderr, "\n❌ 诊断中断: %v\n", err)
		os.Exit(1)
	}
}

func run(username string, listCourses int) error {
	cfg, err := config.Load("")
	if err != nil {
		return err
	}

	fmt.Println("================ courseflow doctor ================")
	if cfg.SourceFile == "" {
		fmt.Println("⚠  没找到配置文件，正在使用内置默认值（密码很可能是空的）")
	} else {
		fmt.Printf("配置来源: %s\n", cfg.SourceFile)
	}
	fmt.Println()

	ctx := context.Background()
	db, err := mysql.New(ctx, cfg.MySQL, zap.NewNop())
	if err != nil {
		return fmt.Errorf("连接 MySQL 失败: %w", err)
	}
	fmt.Println("✅ MySQL 连接成功")

	// ---- 0. 这个库到底有没有迁移过 ----
	fmt.Println("\n=== 0. 迁移记录（goose_db_version）===")
	var versions []struct {
		VersionID int64 `gorm:"column:version_id"`
		IsApplied int   `gorm:"column:is_applied"`
	}
	if err := db.Raw(
		`SELECT version_id, is_applied FROM goose_db_version ORDER BY version_id`).
		Scan(&versions).Error; err != nil {
		fmt.Println("❌ 读不到 goose_db_version 表 —— 这个库里从来没跑过 goose")
		fmt.Println("   → 后端连的库，和你在改的库很可能不是同一个")
	} else if len(versions) == 0 {
		fmt.Println("❌ goose_db_version 是空的 —— 没有应用任何迁移")
	} else {
		for _, v := range versions {
			state := "Applied"
			if v.IsApplied == 0 {
				state = "Rolled back"
			}
			fmt.Printf("   版本 %-3d %s\n", v.VersionID, state)
		}
		fmt.Println("   期望：1~5 全部 Applied")
	}

	// ---- 1. 权限点存在吗 ----
	fmt.Println("\n=== 1. 权限点 ===")
	var permID int64
	if err := db.Raw(`SELECT id FROM permissions WHERE code = ?`, targetPerm).
		Scan(&permID).Error; err != nil {
		return err
	}
	if permID == 0 {
		fmt.Printf("❌ permissions 里没有 %q\n", targetPerm)
		fmt.Println("   → 迁移 0005 没应用。执行: goose ... up")
	} else {
		fmt.Printf("✅ %s 存在（id=%d）\n", targetPerm, permID)
	}

	var totalPerms int64
	_ = db.Raw(`SELECT COUNT(*) FROM permissions`).Scan(&totalPerms).Error
	fmt.Printf("   permissions 总数: %d（期望 20 = 19 个原有 + offering:import）\n", totalPerms)

	// ---- 2. 授给了哪些角色 ----
	fmt.Println("\n=== 2. 该权限授予的角色 ===")
	var grantedRoles []string
	if err := db.Raw(`
		SELECT r.code
		  FROM role_permissions rp
		  JOIN roles r       ON r.id = rp.role_id
		  JOIN permissions p ON p.id = rp.permission_id
		 WHERE p.code = ?
		 ORDER BY r.code`, targetPerm).Scan(&grantedRoles).Error; err != nil {
		return err
	}
	if len(grantedRoles) == 0 {
		fmt.Println("❌ 没有任何角色被授予这个权限")
		fmt.Println("   → 0005 的授权语句没生效（permissions 里没这一行时 INSERT..SELECT 会选到空集）")
	} else {
		fmt.Printf("✅ 已授予: %v\n", grantedRoles)
	}

	// ---- 3. 教师账号概览 ----
	fmt.Println("\n=== 3. 教师账号 ===")
	var teacherCount int64
	_ = db.Raw(`
		SELECT COUNT(*)
		  FROM users u
		  JOIN user_roles ur ON ur.user_id = u.id
		  JOIN roles r       ON r.id = ur.role_id
		 WHERE r.code = 'teacher'`).Scan(&teacherCount).Error
	fmt.Printf("   带 teacher 角色的账号数: %d\n", teacherCount)

	if teacherCount > 0 {
		var names []string
		_ = db.Raw(`
			SELECT u.username
			  FROM users u
			  JOIN user_roles ur ON ur.user_id = u.id
			  JOIN roles r       ON r.id = ur.role_id
			 WHERE r.code = 'teacher'
			 ORDER BY u.id
			 LIMIT 5`).Scan(&names).Error
		fmt.Printf("   前几个: %v\n", names)
	}

	// ---- 4. 指定账号的最终权限 ----
	if username != "" {
		checkUser(db, username)
	}

	// ---- 5. 课程号清单 ----
	if listCourses > 0 {
		listCourseCodes(db, listCourses)
	}

	if username == "" && listCourses == 0 {
		fmt.Println("\n提示：")
		fmt.Println("  -user <账号>     检查某个账号登录后会拿到什么权限")
		fmt.Println("  -courses <N>     列出前 N 个课程号（导入课表要用）")
	}

	fmt.Println("\n================ 诊断结束 ================")
	return nil
}

// listCourseCodes 列出可用的课程号。
//
// 教师导入课表时只提交课程号，所以这些号码必须已经存在于 courses 表里 ——
// 这个清单就是"导入时能填什么"的答案。
func listCourseCodes(db *gorm.DB, limit int) {
	fmt.Printf("\n=== 5. 课程号清单（前 %d 个）===\n", limit)

	var total int64
	_ = db.Raw(`SELECT COUNT(*) FROM courses`).Scan(&total).Error
	fmt.Printf("   courses 总数: %d\n", total)

	if total == 0 {
		fmt.Println("   ❌ 一门课都没有 —— 导入课表必然失败")
		fmt.Println("   → 先去 /admin/courses 建课程，或跑 go run ./cmd/seeddemo")
		return
	}

	var rows []struct {
		Code     string  `gorm:"column:code"`
		Name     string  `gorm:"column:name"`
		Credits  float64 `gorm:"column:credits"`
		DeptName string  `gorm:"column:department"`
	}
	if err := db.Raw(`
		SELECT code, name, credits, department
		  FROM courses
		 ORDER BY code
		 LIMIT ?`, limit).Scan(&rows).Error; err != nil {
		fmt.Printf("   ❌ 查询失败: %v\n", err)
		return
	}

	for _, r := range rows {
		fmt.Printf("   %-8s %-24s %4.1f 学分  %s\n", r.Code, r.Name, r.Credits, r.DeptName)
	}
}

// checkUser 用与登录完全相同的两条 SQL，算出这个账号会拿到的角色和权限。
//
// 刻意不调用 service：这里要验证的是"数据库里的事实"，
// 走业务层反而会把问题掩盖掉。
func checkUser(db *gorm.DB, username string) {
	fmt.Printf("\n=== 4. 账号 %q 登录后会拿到什么 ===\n", username)

	var user struct {
		ID       int64  `gorm:"column:id"`
		Username string `gorm:"column:username"`
		Status   int8   `gorm:"column:status"`
	}
	if err := db.Raw(
		`SELECT id, username, status FROM users WHERE username = ?`, username).
		Scan(&user).Error; err != nil {
		fmt.Printf("❌ 查询账号失败: %v\n", err)
		return
	}
	if user.ID == 0 {
		fmt.Println("❌ 这个账号不存在")
		return
	}
	fmt.Printf("   user_id=%d status=%d\n", user.ID, user.Status)

	var roles []string
	_ = db.Raw(`
		SELECT r.code
		  FROM roles r
		  JOIN user_roles ur ON ur.role_id = r.id
		 WHERE ur.user_id = ?
		 ORDER BY r.code`, user.ID).Scan(&roles).Error
	if len(roles) == 0 {
		fmt.Println("❌ 这个账号没有任何角色 —— user_roles 里没有记录")
	} else {
		fmt.Printf("   角色: %v\n", roles)
	}

	var perms []string
	_ = db.Raw(`
		SELECT DISTINCT p.code
		  FROM permissions p
		  JOIN role_permissions rp ON rp.permission_id = p.id
		  JOIN user_roles ur       ON ur.role_id = rp.role_id
		 WHERE ur.user_id = ?
		 ORDER BY p.code`, user.ID).Scan(&perms).Error
	fmt.Printf("   权限(%d 个): %v\n", len(perms), perms)

	has := false
	for _, p := range perms {
		if p == targetPerm {
			has = true
			break
		}
	}
	if has {
		fmt.Printf("\n✅ 这个账号拥有 %s\n", targetPerm)
		fmt.Println("   → 那么 403 只能是「前端还在用旧 token」：清掉浏览器 localStorage")
		fmt.Println("     里的 courseflow.token，或退出后重新登录。")
	} else {
		fmt.Printf("\n❌ 这个账号没有 %s\n", targetPerm)
		fmt.Println("   → 说明问题在数据层，不在前端。按上面第 0/1/2 步的结论处理。")
	}
}
