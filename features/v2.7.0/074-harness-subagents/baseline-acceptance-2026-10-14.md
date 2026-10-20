> 历史归档：本文保留 2026-10-14 的原始验收口径，不代表当前运行环境、推送状态或迁移 head。最新结论见 [当前验收摘要](./current-acceptance.md)。

# 新基线真实验收补充（2026-10-14）

> 最新增量：执行中崩溃自动接续已开发，并完成主模型/子模型强杀、双 Worker 与重复消息竞争、未知外部调用阻断、用户终止、文件恢复真实联调。最终迁移 head 为 f074_recovery。以 [自动恢复实现与验收](./automatic-recovery-validation.md) 为当前结论；本文其余内容记录此前 E2B 与基线验收。

> DM8 更新（2026-10-14）：已启动独立实例，F074 新增字段的迁移、重复升级、大文本同步/异步读写专项通过。详见 [dm8-validation.md](./dm8-validation.md)。下文“无 DM8 环境”为此前状态，整个平台 DM8 全量验收仍未完成。

当前代码基于 feat/2.7.0/beijing-ns-llm-guardrail（f1b49c51f），所有变更仅保存在本地，未推送远端。

## 环境与迁移

保留原 bisheng 测试库，将其一致性快照导入独立 bisheng_f074_rebase。原库来自 f055 + F074；在副本将版本标记退回 f055（未降级表结构），执行新基线完整中间迁移至 f064 再到 f074，新增 agent_config 列的幂等检查保留数据。最终单 head 为 f074_agent_config。API 与 Worker 均已使用新配置重启；旧库和 SQL 备份保留，未迁移生产数据。

配置/凭据、SQL 备份、原始响应和下载文件均在忽略目录 src/backend/.venv/f074-*，不提交。

## 实际验证

| 场景 | 结果 |
|---|---|
| 真实上传与分析 | 上传包含 4 条销售记录的 f074-sales.csv，解析完成，任务 ef9572cc4ad24ff3a9f635bfeae7c1cd 由计算员在 E2B 中实际读取 CSV 并执行 pandas 汇总，核验员独立核验，主 Agent 汇总。两月各 200，总额 400 |
| 产物回传下载 | 任务 completed，生成 report.md 和 monthly.csv；通过文件下载 API 获取签名路径，从本地 MinIO 读取实际 CSV 字节并解析，断言两行、每月 200、合计 400 |
| E2B 深层目录同步 | 单独实际执行生成 output/nested/proof.csv，返回 new_files 标为产品，并从 WorkspaceBackend 读回精确 value/391 内容，验证真实 E2B → MinIO 工作区链路 |
| 双成员与断线补播 | 针对上述新任务运行 test_harness_live.py，5 passed；覆盖不同成员调用身份、WebSocket 断开后补播顺序完全一致、无效游标和真实 Redis 协议 |
| 人工等待后换进程 | 任务 3f60c5706cbd4143969cea1598eeb4ec 进入 ask_user 等待；终止并重启 API/Worker，确认旧事件 ID 序列保留；回答 A 后继续两个成员调用，completed，结果 399 |
| 工具撤权 | 任务 c22ed9714a8f47efa4c7b70972c7e7e6 在等待点撤权，恢复执行被阻断，HTTP 连接器没有新调用 |
| Skill 禁用 | 任务 f34d3bd274a14838993b32a2acec02c1 在等待点禁用 Skill，恢复执行被阻断，HTTP 连接器没有新调用；已恢复启用、撤销临时权限 |
| 私有补播隔离 | 新完成任务对同租户另一用户、tenant 2 用户均拒绝访问 |

## 本轮修复

- 初始化代码工具先读取工具组配置，避免工作区参数掩盖管理员保存的 E2B 配置。
- Harness 检查穿透 ToolExecutor 包装层，正确识别 E2B 执行器。
- Windows 上传路径转换为沙箱 POSIX 相对路径。
- 工具参数兼容 code 别名，空白代码明确拒绝；无数据时提示主 Agent 澄清，禁止把密钥标识当数据搜索。
- ToolExecutor 转发 close，确保任务结束时释放成员沙箱。
- E2B 文件枚举遍历非隐藏子目录，解决 output/ 及深层产物漏报漏同步；新增贴近真实目录层级的测试。

## 回归和边界

- 后端最终专项含迁移检查：152 passed。
- 新基线真实 Redis/API/WebSocket：5 passed。
- 前端前轮新基线回归：103 passed，类型检查及 i18n 通过；本轮未改前端。
- 全量 frontend lint 仍有新基线自带的 59 项错误，位置及核对证据见 rebase-validation.md。
- 本轮没有重复做浏览器 API 停机交互；此前浏览器自动重连证据与本轮协议测试分开计。未做数小时压力测试。
- E2B 基础调用与真实 CSV 文件链路已通过，不再列为未配置；大文件、超时、多次覆盖同路径的沙箱文件同步不在本轮完整验收范围。
- 长期记忆仍只有 MemoryAdapter，未注入外部提供者；WBS 8.2/8.3 待对接。
- 自动接管已按新增协议实现；未知外部副作用及 E2B 原进程状态仍不自动重放，详见最新报告。
- DM8 新增配置/恢复字段专项已通过；整个平台双库验收及长期记忆依赖不在本次完成范围，T36 不标全通过。

## 执行中崩溃实测补充

实施自动恢复前，子 Agent 执行中强杀 Worker 后重启曾被标 failed，保留 14 条 checkpoint 但没有接管。该历史故障见 [crash-resume-validation.md](./crash-resume-validation.md)；修复后的真实结果见 [automatic-recovery-validation.md](./automatic-recovery-validation.md)。
