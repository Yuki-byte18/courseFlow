package identity

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"go.uber.org/zap"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"courseflow/internal/platform/auth"
)

// dummyHash 是一个固定的 bcrypt 哈希（明文是 password）。
//
// 用途：用户不存在时也照样跑一次 bcrypt 比较，让「用户不存在」和
// 「密码错误」的响应耗时接近。否则攻击者可以用响应时间枚举出哪些
// 用户名是真实存在的。
var dummyHash = []byte("$2a$10$N9qo8uLOickgx2ZMRZoMyeIjZAgcfl7p92ldGxad68LJZdL17lhWy")

// Service 是身份域的业务逻辑层。
type Service struct {
	dao *DAO
	tm  *auth.TokenManager
	log *zap.Logger
}

// NewService 构造 Service。
func NewService(db *gorm.DB, tm *auth.TokenManager, log *zap.Logger) *Service {
	return &Service{dao: NewDAO(db), tm: tm, log: log}
}

// UserSnapshot 是供 seed 之类的外部工具判断账号现状的最小信息。
type UserSnapshot struct {
	ID       int64
	Username string
	HasAdmin bool
}

// FindUserSnapshot 按用户名查账号快照。查不到返回 (nil, nil)。
func (s *Service) FindUserSnapshot(ctx context.Context, username string) (*UserSnapshot, error) {
	u, err := s.dao.FindByUsername(ctx, strings.TrimSpace(username))
	if err != nil {
		return nil, err
	}
	if u == nil {
		return nil, nil
	}
	roles, _, err := s.dao.RolesAndPerms(ctx, u.ID)
	if err != nil {
		return nil, err
	}
	hasAdmin := false
	for _, r := range roles {
		if r == RoleAdmin {
			hasAdmin = true
			break
		}
	}
	return &UserSnapshot{ID: u.ID, Username: u.Username, HasAdmin: hasAdmin}, nil
}

// Register 注册一个学生账号。
//
// 角色固定为 student：教师和管理员必须由管理员创建，
// 这样「注册接口被用来提权」这个问题从根上就不存在。
func (s *Service) Register(ctx context.Context, in RegisterInput) (*AuthResponse, error) {
	username := strings.TrimSpace(in.Username)
	if username == "" {
		return nil, errors.New("用户名不能为空")
	}

	// 预检查只为给出更友好的报错；真正的并发安全靠 username 唯一索引兜底
	existing, err := s.dao.FindByUsername(ctx, username)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return nil, ErrUsernameTaken
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(in.Password), bcrypt.DefaultCost)
	if err != nil {
		return nil, fmt.Errorf("密码加密失败: %w", err)
	}

	user, err := s.dao.CreateUserWithRoles(
		ctx, username, string(hash),
		strings.TrimSpace(in.RealName), strings.TrimSpace(in.Email),
		[]string{RoleStudent},
	)
	if err != nil {
		// 并发注册时后到的那个会撞唯一键，翻译成「用户名已被占用」
		if IsDuplicateKey(err) {
			return nil, ErrUsernameTaken
		}
		return nil, err
	}

	s.log.Info("用户注册成功",
		zap.Int64("user_id", user.ID),
		zap.String("username", user.Username),
	)

	// 注册成功直接签发 token，省掉前端「注册完再登录一次」的一跳
	return s.issueForUser(ctx, user)
}

// Login 校验账号密码并签发 token。
func (s *Service) Login(ctx context.Context, in LoginInput) (*AuthResponse, error) {
	username := strings.TrimSpace(in.Username)

	user, err := s.dao.FindByUsername(ctx, username)
	if err != nil {
		return nil, err
	}

	// 用户不存在时也跑一次 bcrypt，抹平响应时间差异
	if user == nil {
		_ = bcrypt.CompareHashAndPassword(dummyHash, []byte(in.Password))
		s.log.Warn("登录失败：用户不存在", zap.String("username", username))
		return nil, ErrInvalidCredential
	}

	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(in.Password)); err != nil {
		s.log.Warn("登录失败：密码不匹配",
			zap.Int64("user_id", user.ID),
			zap.String("username", username),
		)
		return nil, ErrInvalidCredential
	}

	if !user.IsNormal() {
		s.log.Warn("登录失败：账号被禁用",
			zap.Int64("user_id", user.ID),
			zap.Int8("status", user.Status),
		)
		return nil, ErrUserDisabled
	}

	s.log.Info("登录成功", zap.Int64("user_id", user.ID), zap.String("username", user.Username))
	return s.issueForUser(ctx, user)
}

// Logout 记录退出行为，不签发/撤销任何东西。
//
// JWT 是无状态的：token 一旦签发，在有效期内服务端必须认。
// 所以这里的「退出」只是审计 + 客户端丢弃 token。
// 若确实需要服务端失效，方案是把 jti 写进 Redis 黑名单（TTL = 剩余有效期），
// 代价是每个请求多一次 Redis 查询——留给 M2 按需要再加。
func (s *Service) Logout(ctx context.Context, u *auth.User) error {
	if u == nil {
		return nil
	}
	s.log.Info("用户退出登录",
		zap.Int64("user_id", u.ID),
		zap.String("username", u.Username),
	)
	return nil
}

// CreateUser 由管理员创建账号，可指定一个或多个角色。
//
// 这是系统里唯一能指定角色的入口，调用方必须是持有 user:write 的管理员。
func (s *Service) CreateUser(ctx context.Context, in CreateUserInput) (*UserInfo, error) {
	username := strings.TrimSpace(in.Username)
	if username == "" {
		return nil, errors.New("用户名不能为空")
	}
	if strings.TrimSpace(in.Password) == "" {
		return nil, errors.New("密码不能为空")
	}
	if len(in.Roles) == 0 {
		return nil, errors.New("至少需要指定一个角色")
	}
	for _, r := range in.Roles {
		if !IsKnownRole(r) {
			return nil, fmt.Errorf("未知角色 %q", r)
		}
	}

	existing, err := s.dao.FindByUsername(ctx, username)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return nil, ErrUsernameTaken
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(in.Password), bcrypt.DefaultCost)
	if err != nil {
		return nil, fmt.Errorf("密码加密失败: %w", err)
	}

	user, err := s.dao.CreateUserWithRoles(
		ctx, username, string(hash),
		strings.TrimSpace(in.RealName), strings.TrimSpace(in.Email), in.Roles,
	)
	if err != nil {
		if IsDuplicateKey(err) {
			return nil, ErrUsernameTaken
		}
		return nil, err
	}

	s.log.Info("管理员创建账号成功",
		zap.Int64("user_id", user.ID),
		zap.String("username", user.Username),
		zap.Strings("roles", in.Roles),
	)

	roles, perms, err := s.dao.RolesAndPerms(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	return &UserInfo{
		ID:       user.ID,
		Username: user.Username,
		RealName: user.RealName,
		Roles:    roles,
		Perms:    perms,
	}, nil
}

// issueForUser 查角色权限、签发 token、拼装响应。
func (s *Service) issueForUser(ctx context.Context, user *User) (*AuthResponse, error) {
	roles, perms, err := s.dao.RolesAndPerms(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if roles == nil {
		roles = []string{}
	}
	if perms == nil {
		perms = []string{}
	}

	u := auth.User{
		ID:       user.ID,
		Username: user.Username,
		RealName: user.RealName,
		Roles:    roles,
		Perms:    perms,
	}

	token, exp, err := s.tm.Issue(u)
	if err != nil {
		return nil, fmt.Errorf("签发 token 失败: %w", err)
	}

	return &AuthResponse{
		Token:     token,
		ExpiresAt: exp,
		User: UserInfo{
			ID:       user.ID,
			Username: user.Username,
			RealName: user.RealName,
			Roles:    roles,
			Perms:    perms,
		},
	}, nil
}

// CurrentUserFromToken 把 token 里的身份转成对外视图，不查库。
//
// 用于 GET /me：前端只需要「我是谁、我能干什么」，
// 这些信息已在 token 里，没必要每个请求打一次数据库。
func CurrentUserFromToken(u *auth.User) UserInfo {
	if u == nil {
		return UserInfo{Roles: []string{}, Perms: []string{}}
	}
	roles := u.Roles
	if roles == nil {
		roles = []string{}
	}
	perms := u.Perms
	if perms == nil {
		perms = []string{}
	}
	return UserInfo{
		ID:       u.ID,
		Username: u.Username,
		RealName: u.RealName,
		Roles:    roles,
		Perms:    perms,
	}
}
