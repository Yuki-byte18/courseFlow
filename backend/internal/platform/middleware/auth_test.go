package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/auth"
	"courseflow/internal/platform/config"
)

const testSecret = "unit-test-secret-key-32-chars!!"

func newTestTokenManager(t *testing.T) *auth.TokenManager {
	t.Helper()
	tm, err := auth.NewTokenManager(config.JWTConfig{
		Secret: testSecret,
		Issuer: "courseflow",
		Expire: time.Hour,
	})
	if err != nil {
		t.Fatalf("构造 TokenManager 失败: %v", err)
	}
	return tm
}

// newTestRouter 装配一个用真实中间件链的路由，行为和线上一致。
func newTestRouter(tm *auth.TokenManager) *gin.Engine {
	gin.SetMode(gin.TestMode)
	log := zap.NewNop()

	r := gin.New()
	r.Use(RequestID())

	// 仅要求登录
	r.GET("/me", RequireAuth(tm, log), func(c *gin.Context) {
		u, ok := CurrentUser(c)
		if !ok {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "上下文中没有用户"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"uid": u.ID, "username": u.Username})
	})

	// 要求权限
	r.GET("/admin/offerings", RequireAuth(tm, log), RequirePerm("offering:write"), func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"ok": true})
	})

	// 要求角色
	r.GET("/teacher/roster", RequireAuth(tm, log), RequireRole("teacher", "admin"), func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"ok": true})
	})

	return r
}

func doRequest(r *gin.Engine, path, authorization string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, path, nil)
	if authorization != "" {
		req.Header.Set(authorizationHeader, authorization)
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func TestRequireAuth(t *testing.T) {
	tm := newTestTokenManager(t)
	r := newTestRouter(tm)

	student := auth.User{ID: 1024, Username: "2024001", Roles: []string{"student"}}
	validToken, _, err := tm.Issue(student)
	if err != nil {
		t.Fatalf("签发 token 失败: %v", err)
	}

	expiredToken, _, err := tm.IssueWithTTL(student, -time.Minute)
	if err != nil {
		t.Fatalf("签发过期 token 失败: %v", err)
	}

	cases := []struct {
		name       string
		header     string
		wantStatus int
	}{
		{"没有 Authorization 头", "", http.StatusUnauthorized},
		{"缺少 Bearer 前缀", validToken, http.StatusUnauthorized},
		{"token 是垃圾串", "Bearer not-a-jwt", http.StatusUnauthorized},
		{"token 已过期", "Bearer " + expiredToken, http.StatusUnauthorized},
		{"token 被篡改", "Bearer " + validToken + "x", http.StatusUnauthorized},
		{"正常 token", "Bearer " + validToken, http.StatusOK},
		{"Bearer 小写也接受", "bearer " + validToken, http.StatusOK},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := doRequest(r, "/me", tc.header)
			if w.Code != tc.wantStatus {
				t.Fatalf("状态码 = %d, 期望 %d, body=%s", w.Code, tc.wantStatus, w.Body.String())
			}
		})
	}
}

func TestRequireAuthInjectsUser(t *testing.T) {
	tm := newTestTokenManager(t)
	r := newTestRouter(tm)

	token, _, err := tm.Issue(auth.User{ID: 7, Username: "2024007", Roles: []string{"student"}})
	if err != nil {
		t.Fatalf("签发 token 失败: %v", err)
	}

	w := doRequest(r, "/me", "Bearer "+token)
	if w.Code != http.StatusOK {
		t.Fatalf("状态码 = %d, body=%s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, `"uid":7`) || !strings.Contains(body, "2024007") {
		t.Fatalf("响应里没有正确的用户信息: %s", body)
	}
}

func TestRequirePerm(t *testing.T) {
	tm := newTestTokenManager(t)
	r := newTestRouter(tm)

	noPerm, _, _ := tm.Issue(auth.User{ID: 1, Username: "s1", Roles: []string{"student"},
		Perms: []string{"enrollment:create"}})
	withPerm, _, _ := tm.Issue(auth.User{ID: 2, Username: "a1", Roles: []string{"admin"},
		Perms: []string{"offering:write"}})

	cases := []struct {
		name       string
		header     string
		wantStatus int
	}{
		{"没登录", "", http.StatusUnauthorized},
		{"有登录但没权限", "Bearer " + noPerm, http.StatusForbidden},
		{"有权限", "Bearer " + withPerm, http.StatusOK},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := doRequest(r, "/admin/offerings", tc.header)
			if w.Code != tc.wantStatus {
				t.Fatalf("状态码 = %d, 期望 %d", w.Code, tc.wantStatus)
			}
		})
	}
}

func TestRequireRole(t *testing.T) {
	tm := newTestTokenManager(t)
	r := newTestRouter(tm)

	studentToken, _, _ := tm.Issue(auth.User{ID: 1, Username: "s1", Roles: []string{"student"}})
	teacherToken, _, _ := tm.Issue(auth.User{ID: 2, Username: "t1", Roles: []string{"teacher"}})
	adminToken, _, _ := tm.Issue(auth.User{ID: 3, Username: "a1", Roles: []string{"admin"}})

	cases := []struct {
		name       string
		header     string
		wantStatus int
	}{
		{"学生访问教师接口", "Bearer " + studentToken, http.StatusForbidden},
		{"教师访问", "Bearer " + teacherToken, http.StatusOK},
		{"管理员访问", "Bearer " + adminToken, http.StatusOK},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := doRequest(r, "/teacher/roster", tc.header)
			if w.Code != tc.wantStatus {
				t.Fatalf("状态码 = %d, 期望 %d", w.Code, tc.wantStatus)
			}
		})
	}
}

func TestAuthErrorShape(t *testing.T) {
	tm := newTestTokenManager(t)
	r := newTestRouter(tm)

	w := doRequest(r, "/me", "")
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("状态码 = %d", w.Code)
	}
	body := w.Body.String()
	for _, want := range []string{`"error"`, `"code"`, `"message"`, `"request_id"`} {
		if !strings.Contains(body, want) {
			t.Fatalf("错误响应缺少字段 %s: %s", want, body)
		}
	}
}
