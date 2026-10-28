# MaxKey 用于 SaaS 客服身份层的分支选择与落地分析

## 结论

推荐使用官方仓库 `dromara/MaxKey` 的 **4.2 代码线**：

1. 开发时以官方 `main` 为上游，业务修改保持为小型补丁层。
2. 当前生产基线固定到精确提交 `d7c9d9af1202726a1a54cd60c110dd917b9d93a6`，创建自有 `saas-iam-4.2` 分支和不可变内部 tag/镜像。
3. 后续仅按评审后的 commit/tag 同步上游，不让生产环境自动追随可漂移的 `main`。

该提交比 `4.2.0` 多 3 个提交，其中包含 `adff4ba1`（登录会话二次认证漏洞修复），也包含社交登录依赖迁移。原始 `4.2.0` 缺少已知安全修复，不应直接上线。若明确不使用社交登录且需要最小变更，可采用 `4.2.0 + cherry-pick adff4ba1`，但必须单独完成构建和认证回归测试。

若现有基础设施尚未准备好 JDK 21 / Spring Boot 4，可用 `4.1.x` 当前固定提交 `6cda394e...` 作为 Java 17 备选，并回补二次认证 session 安全修复；不能直接使用原始 `4.1.12`。但新项目长期基线不建议停留在 `4.1.x`，否则会积累一次规模很大的 4.2 迁移。`3.5.x` 和 `3.3.x` 已不适合作为新项目起点。

## 稳定性与二开适配度复核

不存在一个“原样拉取即可安全上线”的官方分支：

- `4.2.0` 是正式版本，但发布后出现了登录会话二次认证安全修复。
- 当前 `main` 已包含该修复，且只比 `4.2.0` 多 3 个提交，但默认 `gradlew test` 在 persistence 模块因未发现测试而失败。
- `4.1.x` 技术栈更成熟，但 2026-06-30 后停止更新，仍包含二次认证提前创建普通 session 的问题；其测试发现 11 个用例但全部 NPE 失败。
- 3.x 使用旧 Java/Spring Boot 且多年不活跃，不适合新项目。

综合“维护活跃度、安全修复、多租户改造、未来升级成本、技术栈寿命”，本项目最合适的二开起点是：

```text
官方上游：main
二开基点：d7c9d9af1202726a1a54cd60c110dd917b9d93a6
自有分支：saas-iam-4.2
生产发布：自有不可变 tag + 镜像 digest
```

这里选择的是**固定的 main 提交**，不是让生产环境跟随 `main` 自动更新。首个内部版本应先补齐租户隔离和 OIDC 契约测试，再作为真正的稳定基线。

## 为什么是 4.2

- 4.2.0 是 2026-10-21 发布的正式版本，使用 JDK 21、Spring Boot 4.1、Spring Security 7.1。
- 官方当前开发活动集中在 `main`，4.1.x 是维护线，3.x 已明显停止活跃。
- 4.2 增加了 `instId` 自动填充、分区键等多租户基础设施，适合后续强化租户隔离。
- 4.1 到 4.2 涉及约 3447 个文件变化，越晚迁移成本越高。

但 4.2 的“多租户”不能直接等同于严格 SaaS 行级隔离：部分查询仍不带 `instId`，新增的 `@InjectInst` 机制当前也没有实际使用点。上线前必须做跨租户越权测试。

## 正确的业务边界

### MaxKey 负责

- 客服、客服组管理员、内部运营账号。
- 登录、MFA、账号状态、密码策略。
- 客服所属机构/租户。
- 客服组、角色、应用访问权限。
- OIDC/OAuth token 签发。
- 在受控 claim 中输出客服身份和 SaaS 租户标识。

### SaaS + Chatwoot/OpenIM 负责

- 访客身份和匿名访客标识。
- 客服与访客的一对一绑定。
- 多客服轮流接待、转移、协同关系。
- 会话、消息、队列、接待状态和历史归属。
- 访客到业务租户的归属校验。

不要在 MaxKey 新增“客服-访客关系表”或为访客批量创建 IAM 账号。

## 标识模型

建议明确区分四类标识：

- `client_id`：OIDC 客户端注册标识，只表示哪个系统在发起认证。
- `aud`：接收 token 的 API/资源服务器。
- `instId`：MaxKey 内部机构分区 ID。
- `tenant_code`：SaaS 对外稳定业务租户码。

现有 `mxk_institutions` 没有独立 `tenant_code` 字段。推荐新增唯一的 `tenantCode`，并保持：

```text
MaxKey instId  <->  SaaS tenant_code
```

不要把 OAuth `client_id`、域名或机构显示名当成 `tenant_code`。如果暂不改 MaxKey 数据库，也可由 SaaS 配置中心维护 `instId → tenant_code` 映射，但 token 签发时必须从可信服务端数据解析，不能接受前端提交的租户码。

## OIDC 改造点

### ID Token

`OIDCIdTokenEnhancer` 默认只输出标准时效、签发者、受众、nonce/hash 等字段，没有租户字段。需要增加受 scope/client 白名单控制的 claim：

```json
{
  "sub": "稳定的 IAM 用户 ID",
  "tenant_code": "saas_tenant_001",
  "preferred_username": "agent001",
  "name": "客服姓名"
}
```

### Access Token

默认 TokenService 生成随机 UUID access token，因此 access token 默认是 opaque token，不存在可直接解析的 claims：

- 保持 opaque 模式：SaaS 通过 UserInfo/introspection 获取租户上下文。
- 改为 JWT 模式：接入 JWT converter/TokenEnhancer，并在签名前加入 `tenant_code`。

不能只把 UUID access token Base64 解码后当 JWT 使用，也不能只修改 ID Token 后假定 API access token 已包含租户。

### UserInfo

默认 `OAuthDefaultUserInfoAdapter` 已输出 `institution = userInfo.instId`，但同时返回较多个人资料。建议：

- 新增明确的 `tenant_code`。
- 查询用户时使用 token principal 中的 `username + instId`，不能只按 username 重新查询。
- 按 OIDC scope 做最小字段披露。
- 不把手机号、生日等与聊天鉴权无关的数据默认返回。
- 删除默认返回的在线 session ticket，避免扩大 session 泄露影响。

### Token 校验

SaaS/聊天接入层必须校验：

- JWT 签名和允许的算法。
- `iss`、`aud`、`exp`、`nbf`。
- `tenant_code` 与当前业务数据租户一致。
- `sub` 对应的内部账号仍有效。
- 角色/权限只用于客服能力授权，不用于推断访客关系。

## 客服组建模

- MaxKey Institution：SaaS 租户归属。
- MaxKey Group/Role：客服组、组长、质检、运营等内部权限。
- MaxKey Application/OIDC Client：SaaS 管理端、客服工作台等认证接入方。
- MaxKey Access：组对应用的访问权限。
- SaaS/IM 表：客服组与访客、会话分配、转移、协同。

一个客服服务一个访客，或多个客服协同服务同一访客，都不改变 IAM 模型；只会改变 SaaS/IM 中的会话参与者和分配记录。

当前源码会把 `Apps.id` 直接赋给 OAuth `clientId`，客户端加载和缓存也只按 `client_id`，不校验 `instId`。因此：

- 现状下 `client_id` 必须全局唯一。
- 业务语义上仍需区分 SaaS `app_id` 与 OIDC `client_id`。
- 若一个业务应用需要多个客户端（Web、移动端、不同环境），应增加显式 `app_id ↔ client_id` 关联，而不是继续依赖二者相等。

## 必须处理的隔离风险

MaxKey 4.2 主登录 SQL 按 username/mobile/email 全局查询，不带 `instId`，并取第一条结果。因此有两个可选策略：

1. **推荐的低改造策略**：客服 username、mobile、email 在整个 MaxKey 实例全局唯一。
2. **租户内可重复账号策略**：登录前从可信域名/入口解析租户，所有认证查询强制使用 `username + instId`，并覆盖密码重置、MFA、UserInfo、同步和管理接口。

无论采用哪种策略，都要测试：

- A 租户管理员不能读写 B 租户用户、组、角色、应用。
- A 租户 token 不能访问 B 租户会话。
- 修改请求体中的 `instId/tenant_code` 不能越权。
- UserInfo、密码重置、OTP、同步接口不能跨租户命中账号。

## 分支决策

- **开发上游**：官方 `main`，仅用于同步和评审，不直接部署。
- **当前生产固定点**：`d7c9d9af1202726a1a54cd60c110dd917b9d93a6`，打自有不可变版本。
- **最小补丁备选**：`4.2.0 + adff4ba1`，仅在验证 cherry-pick、构建和认证回归后采用。
- **Java 17 备选**：固定 `6cda394e...` 并回补二次认证 session 修复，仅适合暂时无法升级技术栈的环境。
- **不建议**：直接追随 `main`，因为包含未发布变更。
- **不建议**：`3.5.x`、`3.3.x`，维护状态和技术栈均不适合作为新项目基础。
- **不使用**：`MaxKeyTop/MaxKey`，它是官方源仓库的滞后 fork。

## 最小实施顺序

1. 从固定提交建立自有 4.2 分支和不可变构建产物。
2. 确定全局唯一账号或租户感知登录策略。
3. 增加 `tenantCode` 或可信 `instId → tenant_code` 映射。
4. 为 ID Token、Access Token、UserInfo 增加一致的 `tenant_code`。
5. 在 SaaS 网关统一校验 token 和租户上下文。
6. 在 SaaS/IM 建立访客、客服分配、会话归属和转移模型。
7. 完成跨租户越权、token 篡改、MFA 绕过和回归测试后上线。
