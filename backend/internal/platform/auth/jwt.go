// Package auth 负责 JWT 的签发与校验，以及把「当前用户」放进/取出请求上下文。
//
// 这个包刻意不依赖 gin：业务层的 service 只需要标准库的 context.Context
// 就能拿到当前用户，不绑定到任何 HTTP 框架。
package auth

import (
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"courseflow/internal/platform/config"
)

// 认证相关的哨兵错误。调用方用 errors.Is 判断，不要比对字符串。
var (
	ErrTokenMissing = errors.New("缺少认证凭据")
	ErrTokenExpired = errors.New("认证凭据已过期")
	ErrTokenInvalid = errors.New("认证凭据无效")
)

// User 是认证通过后随请求流动的身份信息。
//
// Perms 直接来自 JWT：鉴权时不需要查库，这对抢课这种高并发路径很重要。
// 代价是权限变更后要等 token 过期才生效（我们设 2 小时，可接受）。
type User struct {
	ID       int64    `json:"uid"`
	Username string   `json:"username"`
	RealName string   `json:"real_name,omitempty"`
	Roles    []string `json:"roles,omitempty"`
	Perms    []string `json:"perms,omitempty"`
}

// HasRole 判断用户是否拥有任意一个给定角色。
func (u *User) HasRole(codes ...string) bool {
	if u == nil {
		return false
	}
	for _, want := range codes {
		for _, have := range u.Roles {
			if have == want {
				return true
			}
		}
	}
	return false
}

// HasPerm 判断用户是否拥有任意一个给定权限码。
func (u *User) HasPerm(codes ...string) bool {
	if u == nil {
		return false
	}
	for _, want := range codes {
		for _, have := range u.Perms {
			if have == want {
				return true
			}
		}
	}
	return false
}

// Claims 是 JWT 的载荷。
// User 内嵌进来，字段会被平铺到 JWT 顶层，不额外多一层嵌套。
type Claims struct {
	User
	jwt.RegisteredClaims
}

// TokenManager 负责签发与校验 JWT。
type TokenManager struct {
	secret []byte
	issuer string
	ttl    time.Duration
}

// NewTokenManager 用配置构造一个 TokenManager。
func NewTokenManager(cfg config.JWTConfig) (*TokenManager, error) {
	if len(cfg.Secret) < 16 {
		return nil, errors.New("jwt.secret 至少需要 16 个字符")
	}
	if cfg.Expire <= 0 {
		return nil, errors.New("jwt.expire 必须大于 0")
	}
	return &TokenManager{
		secret: []byte(cfg.Secret),
		issuer: cfg.Issuer,
		ttl:    cfg.Expire,
	}, nil
}

// TTL 返回配置的有效期。
func (m *TokenManager) TTL() time.Duration { return m.ttl }

// Issue 用默认有效期签发 token，返回 token 字符串和过期时间。
func (m *TokenManager) Issue(u User) (string, time.Time, error) {
	return m.IssueWithTTL(u, m.ttl)
}

// IssueWithTTL 用指定有效期签发 token。
// 单独暴露出来是为了测试能造出「已过期」的 token，不必 sleep。
func (m *TokenManager) IssueWithTTL(u User, ttl time.Duration) (string, time.Time, error) {
	now := time.Now()
	exp := now.Add(ttl)

	claims := Claims{
		User: u,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    m.issuer,
			Subject:   strconv.FormatInt(u.ID, 10),
			IssuedAt:  jwt.NewNumericDate(now),
			NotBefore: jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(exp),
		},
	}

	signed, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(m.secret)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("签发 token 失败: %w", err)
	}
	return signed, exp, nil
}

// Parse 校验 token 并返回其中的用户身份。
//
// 用 WithValidMethods 显式限定算法：否则攻击者可以把 header 改成 alg=none
// 或换成非对称算法，让服务端用错误的密钥去验签。
func (m *TokenManager) Parse(raw string) (*User, error) {
	claims := &Claims{}
	_, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) {
		return m.secret, nil
	},
		jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}),
		jwt.WithIssuer(m.issuer),
	)
	if err != nil {
		switch {
		case errors.Is(err, jwt.ErrTokenExpired):
			return nil, ErrTokenExpired
		case errors.Is(err, jwt.ErrTokenMalformed), errors.Is(err, jwt.ErrTokenSignatureInvalid),
			errors.Is(err, jwt.ErrTokenNotValidYet), errors.Is(err, jwt.ErrTokenInvalidIssuer):
			return nil, ErrTokenInvalid
		default:
			return nil, fmt.Errorf("%w: %v", ErrTokenInvalid, err)
		}
	}
	// Claims 同时内嵌了 User 和 jwt.RegisteredClaims，两者的 ID 字段同名，
	// 所以这里必须用 claims.User.ID 显式限定，不能用 claims.ID。
	if claims.User.ID <= 0 {
		return nil, ErrTokenInvalid
	}
	return &claims.User, nil
}
