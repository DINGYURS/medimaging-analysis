# 数据库表设计

## users：用户账号

2026-11-07 对照服务器 MySQL 的 SHOW CREATE TABLE users 核对。本文记录当前账号表设计；其他业务表以 backend/app/db/models.py 为准。

| 字段 | MySQL 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| id | bigint | 否 | 自增主键 |
| username | varchar(64) | 否 | 登录账号，唯一索引 ix_users_username |
| hashed_password | varchar(255) | 否 | bcrypt 密码哈希 |
| real_name | varchar(64) | 否 | 用户姓名 |
| role | varchar(20) | 否 | ADMIN / DOCTOR，普通索引 ix_users_role |
| is_active | tinyint(1) | 否 | 账号启用状态 |
| last_login_at | datetime | 是 | 最近登录时间 |
| created_at | datetime | 否 | 创建时间 |
| updated_at | datetime | 否 | 更新时间 |

表使用 InnoDB，字符集 utf8mb4，排序规则 utf8mb4_unicode_ci。除自增主键及可空字段外，非空字段由应用写入；role、is_active 和创建/更新时间的默认值由 ORM 提供，并非数据库 DEFAULT。

## 账号业务规则

- 自助注册只接受登录账号、姓名和密码；账号固定为启用状态的 DOCTOR。
- 管理员开通医生账号只填写登录账号、姓名和初始密码。
- 管理员账号列表显示登录账号、姓名、角色、账号状态和最后登录时间；搜索匹配账号和姓名。
- users 表及用户请求、响应模型均不再包含 department 字段。

## 数据库变更记录

2026-11-07：统一移除科室业务，删除 users.department 列。使用 scripts/drop_user_department.py --apply 执行；执行前将原始建表语句及用户主键、账号、科室值备份到 .deployment 下的独立目录，并对其余字段执行前后摘要校验。迁移不删除用户记录，不修改密码、角色或账号状态。
