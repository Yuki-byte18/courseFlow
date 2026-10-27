package identity

// 身份域的业务错误。
//
// 目前直接定义在本包；等错误码统一到 platform/errs 之后会迁过去。
// 关键点：这些错误对外只暴露「不含细节」的文案，避免泄露用户是否存在。
import "errors"

var (
	// ErrUsernameTaken 注册时用户名已被占用。
	ErrUsernameTaken = errors.New("用户名已被占用")

	// ErrInvalidCredential 登录时用户名不存在或密码不匹配。
	// 两种情况故意合并成同一个错误，防止被用来枚举账号。
	ErrInvalidCredential = errors.New("用户名或密码错误")

	// ErrUserDisabled 账号被管理员禁用。
	ErrUserDisabled = errors.New("账号已被禁用，请联系管理员")

	// ErrRoleNotFound 注册时要绑定的角色在 roles 表里不存在。
	ErrRoleNotFound = errors.New("角色不存在，请先执行权限初始化迁移")
)
