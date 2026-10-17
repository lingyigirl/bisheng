# 远端同步与本地验证（2026-10-18）

## 合并范围

- 当前分支：`feat/2.7.0/beijing-ns-llm-guardrail`。
- 同步远端跟踪分支至 `519c252a7`，新增开放 API 鉴权、评测能力与 Linsight 队列命名空间配置等改动，共涉及 145 个文件。
- 合并前本地 HEAD：`3aa4c15f4`，5 个本地提交保留；备份分支：`codex/backup-before-pull-3aa4c15f4`。
- 仅 fetch 和本地 merge，未向远端推送。

## 冲突处理

- `release-contract.md`：同时保留 F074 Harness 与 F076 开放 API 的对象归属、历史记录和不变量；保持历史记录表连续。
- `packages/locales/src/api_errors/{en,ja,zh-Hans}.json`：同时保留 Harness 的 `11071` 和开放 API 的 `11801`–`11821`；生成产物通过构建工具检查，无手工修改产物。
- 自动合并的 Linsight API 与 Worker 均使用 `build_linsight_queue`，保留本地恢复协议与续聊输入持久化逻辑。
- 无未解决冲突，暂存区 `git diff --check` 通过。

## 检查结果

- 后端：`test/linsight`、`test/open_api`、本次新增/修改的评测测试、迁移单头和 DeepSeek 多模态测试，共 **1135 passed / 8 skipped / 0 failed**，134.62 秒。
- 前端：成员配置、任务输入、会话和 WebSocket 相关 **8 suites / 37 passed**。
- 根目录 `pnpm typecheck`、`pnpm locales:check`、`pnpm check-i18n` 和 client lint 通过。
- 根目录 lint 在 platform 阶段仍有既有 **54 个 no-explicit-any 错误**，与合并前基线一致；未宣称整个前端 lint 门禁通过。
- 本地执行 `alembic upgrade head`，通过 metadata 创建开放 API 的 `api_credential`、`open_api_tenant_setting`、`service_account` 三张缺失表。

## 启动与真实任务

- API、Worker、platform 和 client 均通过分离进程重新启动；API 使用既有 `.venv/f074-rebase-runtime.yaml`。
- 前端通过本地启动进程环境覆盖 API/MinIO 代理地址，匹配本地业务 MinIO `127.0.0.1:9100`；未将远端提交的环境文件改成本机配置。
- API `:7860` 健康检查 200；platform `:3001` 与 client `:4001/workspace/` 返回 200。
- 登录态下 `GET /api/v1/service-accounts` 返回 HTTP 200 / 业务码 200；未携带服务账号密钥的 `GET /api/v2/auth/whoami` 返回 HTTP 401 / 业务码 11801。
- 真实任务：`856080e28cc14a248df484482536d39c`，会话：`888b6f82f2fb4abeae927f83c9a27000`。
- 主模型 `3` 成功调度配置成员 `lib-7`（独立模型 `4`），成员返回 `PULL-1018-CHILD-OK`；主 Agent 写入 `output/pull-1018-smoke.txt`，任务终态 completed，结果为 `PULL-1018-MERGE-OK`。
- 浏览器显示成员完成、任务完成及新文件；实际下载后核对文件内容为 `PULL-1018-MERGE-OK`。
- WebSocket 主动断开后从游标接续，核对 **464 条事件，无重复、无遗漏**；无效游标按预期返回 replay_reset。

日志保存在 `src/backend/.venv/f074-logs/pull-1018-*`。任务快照与重连证据位于 `.venv/f074-evidence/review-1018-pull-*`；浏览器截图为 `.venv/f074-evidence/pull-1018-browser-complete.jpg`。运行配置、凭据、下载文件、日志和截图均不进入提交。

本次未重新进行数小时长稳、物理断网、真实强杀 Worker 或完整 DM8 验收；既有崩溃恢复回归已包含在后端测试中。
