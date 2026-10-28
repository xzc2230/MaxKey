# MaxKey 单机 Docker Compose 部署设计

## 1. 目标与适用范围

本方案用于 `saas-iam-4.2` 分支的首期部署，目标环境为一台 8 核 16 GB AWS EC2，操作系统为 Ubuntu Server 24.04 LTS。首期允许升级期间短暂停止登录服务，MySQL 与 Redis 均部署在同一台服务器。

本方案分为两个阶段：

1. 内网验证阶段：使用服务器 IP 和 HTTP，仅限可信内网访问。
2. 生产阶段：配置认证域名、管理域名和 HTTPS 后，才允许接入正式 OIDC 客户端。

OpenAPI 服务默认不部署。只有确认需要 SCIM、账号同步或开放管理 API 时，才单独评审并启用。

## 2. 版本基线

- Git 分支：`saas-iam-4.2`
- 固定基点：`d7c9d9af1202726a1a54cd60c110dd917b9d93a6`
- Java：21
- MySQL：8.4.x，固定补丁版本
- Redis：7.x，固定补丁版本
- Docker Engine：当前受支持稳定版
- Docker Compose：v2

所有自建镜像使用不可变版本号，例如：

```text
registry.example.internal/maxkey-auth:4.2.0-company.1-d7c9d9af
registry.example.internal/maxkey-mgt:4.2.0-company.1-d7c9d9af
registry.example.internal/maxkey-frontend:4.2.0-company.1-d7c9d9af
registry.example.internal/maxkey-mgt-frontend:4.2.0-company.1-d7c9d9af
```

禁止生产环境使用 `latest`。

## 3. 服务器规格与目录

建议服务器：

- 8 vCPU
- 16 GB RAM
- 200 GB 加密 gp3 EBS
- Ubuntu Server 24.04 LTS
- 绑定 Elastic IP
- 时间同步启用，时区设为 `Asia/Shanghai`

目录规划：

```text
/srv/maxkey/
├── compose.yaml
├── .env
├── images/
├── mysql/
│   ├── data/
│   ├── conf.d/
│   └── init/
├── redis/
│   ├── data/
│   └── redis.conf
├── nginx/
│   ├── conf.d/
│   └── certs/
├── logs/
├── backup/
└── scripts/
```

`.env` 权限必须为 `0600`，不得提交 Git。

## 3.1 AWS 前置条件

EC2 Instance Profile 仅授予 ECR 镜像拉取权限，推荐使用 AWS 托管策略 `AmazonEC2ContainerRegistryReadOnly`。部署脚本通过 Instance Role 获取短期凭据，不接收或保存长期 Access Key。

Security Group：

- SSH 22 仅允许运维固定出口 IP。
- 内网验证期间，HTTP 80 仅允许测试来源 IP。
- 正式上线时开放 HTTPS 443，并关闭公网 HTTP 或只保留 80 到 443 跳转。
- 不开放 3306、6379、9525、9526、9527、8526、8527。

ECR 使用以下固定仓库名：

```text
maxkey/auth
maxkey/mgt
maxkey/frontend
maxkey/mgt-frontend
```

安装器通过 IMDSv2 获取 Region，通过 STS 获取 AWS Account ID，自动拼接 ECR registry 地址。

## 3.2 一键部署发布包

每个内部版本生成一个不可变发布包：

```text
maxkey-deploy-4.2.0-company.1/
├── install.sh
├── compose.yaml
├── .env.template
├── manifest.sha256
├── nginx/
│   └── conf.d/maxkey.conf
├── redis/
│   └── redis.conf
├── mysql/
│   └── init/maxkey-hardened.sql.template
└── scripts/
    ├── healthcheck.sh
    ├── backup.sh
    ├── restore.sh
    └── upgrade.sh
```

发布时同时提供：

```text
maxkey-deploy-4.2.0-company.1.tar.gz
maxkey-deploy-4.2.0-company.1.tar.gz.sha256
```

服务器操作只有四步：

```bash
sha256sum -c maxkey-deploy-4.2.0-company.1.tar.gz.sha256
tar -xzf maxkey-deploy-4.2.0-company.1.tar.gz
cd maxkey-deploy-4.2.0-company.1
sudo ./install.sh
```

外部 SHA-256 文件校验压缩包，包内 `manifest.sha256` 由安装器再次校验所有部署文件。

## 3.3 install.sh 职责

安装器按以下顺序执行：

1. 获取文件锁，阻止两个安装进程并发运行。
2. 校验 Ubuntu 24.04、amd64、8 CPU、16 GB RAM、可用磁盘和端口。
3. 安装 Docker Engine、Docker Compose v2、AWS CLI、`apache2-utils` 和必要系统工具。
4. 启动并设置 Docker 开机自启。
5. 验证 EC2 Instance Role、STS 身份与 ECR ReadOnly 权限。
6. 使用 IMDSv2 获取 Region，登录当前 AWS Account 的 ECR。
7. 创建 `/srv/maxkey` 目录，权限设置为 root 管理。
8. 首次安装时生成 MySQL、Redis 和应用随机密码。
9. 生成 24 位随机管理员密码，使用 `htpasswd -bnBC 12` 生成 bcrypt hash，并替换 hardened SQL 模板中的 `__MAXKEY_ADMIN_BCRYPT__`。
10. 将服务密码写入 `/srv/maxkey/.env`，权限设为 `0600`。
11. 将管理员初始密码写入 `/root/maxkey-initial-credentials.txt`，权限设为 `0600`；首次成功登录并改密后必须删除。
12. 写入镜像版本和 ECR registry，不允许 `latest`。
13. 执行 `docker compose pull`，并记录实际镜像 digest。
14. 启动 MySQL、Redis并等待健康检查。
15. 首次安装时由 MySQL 自动执行 hardened 初始化 SQL。
16. 启动认证后端、管理后端、两个前端和入口 Nginx。
17. 运行 `healthcheck.sh`，验证容器、HTTP、MySQL、Redis和基础登录页。
18. 输出访问地址、镜像 digest、备份命令和初始凭据文件位置。

安装器退出码：

- `0`：安装或配置对齐成功。
- `10`：系统前置条件不满足。
- `20`：AWS IAM/ECR 校验失败。
- `30`：MySQL/Redis 启动或初始化失败。
- `40`：应用启动或健康检查失败。
- `50`：检测到已有数据，当前操作可能破坏数据并被拒绝。

安装失败时保留 MySQL/Redis 数据卷和诊断日志，不自动删除数据。

## 3.4 幂等与重复执行

`install.sh` 必须支持重复执行：

- Docker 已安装时只校验版本和服务状态。
- `.env` 已存在时不重新生成密码。
- MySQL 数据目录非空时不重复执行初始化 SQL。
- 镜像版本相同时不重建容器。
- 配置发生变化时仅执行 `docker compose up -d` 对齐。
- 检测到数据库结构升级时拒绝继续，并提示使用 `scripts/upgrade.sh`。

卸载、清空数据库和重新初始化不属于 `install.sh` 能力，必须通过单独的破坏性命令并要求人工二次确认。

## 4. 运行组件

### 4.1 Nginx

职责：

- 唯一对外入口。
- 内网验证阶段监听 HTTP。
- 生产阶段终止 TLS。
- 转发认证后端、认证前端、管理后端和管理前端。
- 对管理路径配置 VPN 或来源 IP 白名单。

仅宿主机开放 80/443。其他组件不映射宿主机端口。

### 4.2 MaxKey 认证服务

- 容器内端口：9527
- Context Path：`/sign`
- 内存限制：3 GB
- JVM 建议：`-Xms512m -Xmx2048m`
- 使用 MySQL 保存身份数据。
- `SERVER_PERSISTENCE=2`，使用 Redis 保存 token/session。

### 4.3 MaxKey 管理服务

- 容器内端口：9526
- Context Path：`/maxkey-mgt-api`
- 内存限制：2 GB
- JVM 建议：`-Xms256m -Xmx1536m`
- 仅允许管理员通过可信网络访问。

### 4.4 前端

- 认证前端路径：`/maxkey`
- 管理前端路径：`/maxkey-mgt`
- 前端镜像仅包含构建后的 Angular 静态资源与 Nginx。

### 4.5 MySQL

- 不映射宿主机 3306。
- 数据库名：`maxkey`
- 应用账号：`maxkey_app`
- 禁止应用使用 root。
- 数据目录使用宿主机持久卷。
- 字符集：`utf8mb4`
- 排序规则：`utf8mb4_unicode_ci`
- 内存上限建议 4 GB。

仓库初始化 SQL 包含已知默认管理员凭据和演示账号。生产初始化前必须生成 hardened SQL：

- 为管理员生成新的 bcrypt 密码。
- 删除或禁用演示账号。
- 删除不需要的演示应用和回调地址。
- 保留系统运行必需的角色、权限和配置数据。

初始化 SQL 只能在空数据卷首次启动时执行。

### 4.6 Redis

- 不映射宿主机 6379。
- 开启强密码。
- 开启 AOF：`appendonly yes`。
- 数据目录使用宿主机持久卷。
- 内存上限建议 1 GB。
- 配置合理的 `maxmemory-policy`，避免无提示删除活跃 session。

Redis 用于 token/session，不代替 MySQL 备份。

## 5. Docker 镜像修正

现有后端 Dockerfile 存在两个部署问题：

1. 固定 `sleep 60` 等待依赖。
2. 使用 `java -jar app.jar $JAVA_OPTS`，JVM 参数位于 `-jar` 之后，不会作为 JVM 参数生效。

应改为 exec 形式：

```text
java $JAVA_OPTS -jar app.jar
```

Compose 使用 MySQL、Redis healthcheck 和条件依赖控制启动顺序，不使用固定休眠。

后端镜像使用 JRE 21 运行时镜像；构建在独立 CI/构建阶段完成，不在生产服务器安装 Gradle、Node.js 或 pnpm。

## 6. 网络与访问路径

Compose 创建两个网络：

- `maxkey_edge`：Nginx 与前后端服务。
- `maxkey_data`：后端、MySQL、Redis，设置为 internal network。

内网验证阶段：

```text
http://SERVER_IP/maxkey/
http://SERVER_IP/sign/
http://SERVER_IP/maxkey-mgt/
http://SERVER_IP/maxkey-mgt-api/
```

生产阶段建议使用两个域名：

```text
https://sso.example.com/maxkey/
https://sso.example.com/sign/
https://iam-admin.example.com/maxkey-mgt/
https://iam-admin.example.com/maxkey-mgt-api/
```

OIDC issuer、CAS URL、redirect URI 和 Cookie 策略必须在启用生产域名后统一调整。裸 IP/HTTP 只允许功能验证，不作为正式身份提供方地址。

## 7. 配置与密钥

至少通过 `.env` 或 Docker secret 提供：

```text
DATABASE_HOST
DATABASE_PORT
DATABASE_NAME
DATABASE_USER
DATABASE_PWD
REDIS_HOST
REDIS_PORT
REDIS_PWD
SERVER_DOMAIN
SERVER_PERSISTENCE=2
LOGIN_CAS_SERVER
LOGIN_CAS_SERVICE
```

要求：

- 数据库、Redis、OIDC client secret 使用不同随机值。
- 密钥不写入镜像、Compose 文件或 Git。
- 禁止保留仓库默认密码。
- JWT/OIDC 签名密钥独立备份并限制读取权限。
- 密钥轮换前验证旧 token 和客户端兼容策略。

## 8. 构建与发布

推荐由 CI 或隔离构建机完成：

1. 检出固定 commit。
2. 运行后端编译和项目自建测试。
3. 构建认证与管理端 bootJar。
4. 使用 pnpm 锁定依赖并构建两个 Angular production bundle。
5. 构建四个业务镜像。
6. 扫描镜像和依赖漏洞。
7. 推送私有镜像仓库并记录 digest。
8. 生产服务器仅执行 `docker compose pull` 和 `docker compose up -d`。

若首期没有私有镜像仓库，允许使用 `docker save`、校验 SHA-256 后离线传输并 `docker load`；仍不得在生产服务器直接从不固定的源码构建。

## 9. 启动顺序与健康检查

启动顺序：

1. MySQL、Redis。
2. MySQL 初始化完成，Redis `PING` 成功。
3. MaxKey 认证服务、管理服务。
4. 认证和管理后端健康检查通过。
5. 两个前端。
6. Nginx。

仓库默认关闭 Actuator endpoint。首期可使用 TCP/HTTP 路径做容器健康检查；二开阶段应增加不泄露配置的专用 liveness/readiness endpoint。

健康检查失败时容器不得被加入 Nginx 可用上游。

## 10. 备份与恢复

MySQL：

- 每日执行一致性全量备份。
- 本机保留 14 天。
- 每周至少复制一份到服务器之外。
- 备份文件加密并记录 SHA-256。
- 每月至少进行一次恢复演练。

Redis：

- 开启 AOF。
- 在维护操作前保存快照。
- Redis 恢复失败时允许用户重新登录，不以牺牲 MySQL 一致性换取 session 恢复。

同时备份：

- Compose 文件。
- Nginx 配置与证书。
- hardened 初始化 SQL。
- OIDC/JWT 签名密钥。
- `.env` 的加密副本。
- 当前镜像 tag 和 digest 清单。

## 11. 发布与回滚

发布：

1. 宣布维护窗口。
2. 备份 MySQL、配置和密钥。
3. 验证备份可读取。
4. 拉取固定 digest 镜像。
5. 审核数据库升级 SQL。
6. 停止应用写流量。
7. 执行数据库迁移。
8. 启动新容器。
9. 执行冒烟测试。
10. 恢复流量。

回滚：

- 未修改数据库结构时，直接恢复旧镜像 digest。
- 数据库变更向后兼容时，可恢复旧镜像并保留数据库。
- 数据库变更不兼容时，必须恢复数据库备份和旧镜像。

禁止只回滚应用而忽略不兼容数据库变更。

## 12. 监控与日志

首期至少监控：

- 容器存活与重启次数。
- HTTP 5xx 和登录失败率。
- JVM 堆、GC、线程数。
- MySQL 连接、慢查询、磁盘空间。
- Redis 内存、连接数、AOF 状态。
- TLS 证书到期时间。
- 宿主机 CPU、内存、磁盘和 inode。

日志要求：

- Docker 日志启用大小和文件数轮转。
- 禁止记录密码、client secret、完整 token、短信验证码。
- 登录审计日志与普通运行日志分开保留。
- 服务器时间必须同步，便于审计和 OIDC token 时效校验。

## 13. 上线验收

功能验收：

- 认证前端和管理端可访问。
- 管理员首次登录后已修改默认凭据。
- 客服账号可完成登录和 MFA。
- OIDC authorization code + PKCE 流程成功。
- ID Token/UserInfo 中的 `tenant_code` 正确。
- token 过期、刷新和退出行为正确。

安全验收：

- 宿主机只暴露批准的 SSH、HTTP/HTTPS 端口。
- MySQL、Redis和应用内部端口无法从外部访问。
- A 租户 token 无法访问 B 租户资源。
- 修改请求中的 `instId/tenant_code` 不能越权。
- 管理端只允许可信网络访问。
- 默认账号、演示账号和演示应用已处理。

恢复验收：

- 可从备份恢复 MySQL。
- 可使用旧镜像 digest 回滚。
- Redis 数据丢失时系统允许重新登录且身份数据不丢失。

## 14. 当前明确不做的内容

- Kubernetes/Helm。
- 跨主机高可用。
- OpenAPI 默认公网开放。
- 在 IAM 中保存访客、聊天会话或客服—访客关系。
- 使用仓库原始 `docker-compose.yml` 直接上线。
