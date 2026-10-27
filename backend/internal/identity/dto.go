package identity

// RegisterRequest 是注册入参。
//
// 注意：这里【没有】role 字段。注册只允许创建学生账号，
// 教师和管理员必须由管理员在后台创建——否则谁都能注册成管理员。
type RegisterRequest struct {
	Username string `json:"username" binding:"required,min=4,max=64"`
	// bcrypt 只处理前 72 字节，超长会被静默截断，所以这里限死 max=72
	Password string `json:"password" binding:"required,min=8,max=72"`
	RealName string `json:"real_name" binding:"required,min=1,max=64"`
	Email    string `json:"email" binding:"omitempty,email,max=128"`
}

// LoginRequest 是登录入参。
type LoginRequest struct {
	Username string `json:"username" binding:"required"`
	Password string `json:"password" binding:"required"`
}

// CreateUserRequest 是管理员建号入参。
//
// 这里是唯一可以指定角色的入口，且它本身受 user:write 权限保护。
// 密码只要求 6 位：管理员建的是"初始密码"，理应首次登录后修改。
type CreateUserRequest struct {
	Username string   `json:"username" binding:"required,min=4,max=64"`
	Password string   `json:"password" binding:"required,min=6,max=72"`
	RealName string   `json:"real_name" binding:"required,min=1,max=64"`
	Email    string   `json:"email" binding:"omitempty,email,max=128"`
	Roles    []string `json:"roles" binding:"required,min=1,max=3,dive,oneof=student teacher admin"`
}

// RegisterInput 是 service 层接收的注册命令。
type RegisterInput struct {
	Username string
	Password string
	RealName string
	Email    string
}

// LoginInput 是 service 层接收的登录命令。
type LoginInput struct {
	Username string
	Password string
}

// CreateUserInput 是 service 层接收的建号命令。
type CreateUserInput struct {
	Username string
	Password string
	RealName string
	Email    string
	Roles    []string
}
