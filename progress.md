# Progress

## 2026-10-28
- 已初始化分析计划。
- 已将官方 `dromara/MaxKey` 拉取到 `source/`，当前 main HEAD 为 `d7c9d9af...`。
- 已显式拉取正式标签 `4.2.0`，并核验安全修复 `adff4ba1` 位于该标签之后。
- 已完成 main/4.2、4.1.x、3.x 的维护状态与技术栈比较。
- 已核对 OIDC ID Token、Access Token、UserInfo、多租户字段和登录查询实现。
- 已输出 `MAXKEY_SAAS_IAM_ANALYSIS.md`。
- 已根据并行复核结果修正文档：默认 access token 为 opaque UUID；生产固定点改为包含安全修复的 `d7c9d9af...`；补充 OAuth client、UserInfo 和 `app_id/client_id` 隔离风险。
- 已复核稳定性：main 与 4.1.x 默认测试任务均不绿色；4.1.x 仍缺二次认证 session 修复。
- 最终二开主选固定为 `d7c9d9af...` 后建立自有 `saas-iam-4.2`；Java 17 备选为 `6cda394e...` 并回补安全修复。
- 已在 `source/` 从 `d7c9d9af...` 创建并切换到本地分支 `saas-iam-4.2`。
- 已完成 Linux 单机 Docker Compose 部署设计并提交为 `3c099711`，等待用户审核设计后再编写实施计划。
- 已按 AWS EC2 Ubuntu 24.04 + ECR 补充一键安装器设计并提交为 `58b5a308`，等待用户审核后编写具体实施计划。
- 用户已确认部署设计；已完成实施计划 `docs/superpowers/plans/2026-10-28-maxkey-aws-compose-deployment.md`。
- 实施计划经三轮独立复核，已补齐镜像 digest 固定、前端基础镜像、双网络、MySQL 中断保护、管理员引导密码、加密备份、S3 最小权限、回滚与测试环境等约束；无剩余阻断/高风险问题。
- 当前等待选择实施执行方式；尚未修改部署代码。
