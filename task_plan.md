# MaxKey 分支与 SaaS IAM 适配分析

## 目标
拉取 MaxKey 官方开源仓库，基于代码与维护状态，推荐最适合“仅管理客服内部账号、OIDC 输出 SaaS 业务标识、访客与会话关系由业务/IM 维护”的分支，并给出落地方案。

## 阶段
- [complete] 1. 确认官方仓库、远程分支、标签和维护状态
- [complete] 2. 检查多租户、OIDC Claim、应用与用户组织模型
- [complete] 3. 对比候选分支的稳定性、改造成本与兼容风险
- [complete] 4. 输出推荐分支、架构边界和实施建议
- [complete] 5. 复核 4.1/4.2 稳定性与二次开发适配度
- [complete] 6. 从固定提交创建 `saas-iam-4.2` 二开分支
- [complete] 7. 设计 Linux 单机 Docker Compose 部署方案
- [complete] 8. 编写并复核 AWS EC2 一键部署实施计划
- [pending] 9. 按实施计划开发、测试并完成新 EC2 验收

## 决策准则
- 优先官方持续维护的稳定代码线，不以历史分支名称推断能力
- IAM 只管理内部账号，不把访客或聊天关系塞进 IAM
- 必须能安全地将 tenant_code/app_id 作为受控 Claim 输出
- 区分 OAuth/OIDC 的 client_id 与业务 app_id，避免概念冲突

## 错误记录
- GitHub CLI `gh` 未安装，无法用 CLI 读取仓库元数据；改用 Git 远程引用和仓库源码/公开 API 交叉验证。
- 稳定性复核时本地尚未拉取 `4.1.12` tag，首次 `git log 4.1.12..origin/4.1.x` 失败；将显式 fetch 后重试。
- main 的 `gradlew test` 在 `:maxkey-persistence:test` 因“测试源码存在但未发现测试”失败；记录为上游测试配置问题，不能声称 main 测试通过。
- 4.1.x 的 `gradlew test` 发现 11 个 persistence 测试但全部 NPE 失败；两分支都没有可直接依赖的绿色测试基线。
