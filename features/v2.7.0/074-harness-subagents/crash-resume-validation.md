# 执行中 Worker 崩溃自动续跑验证

日期：2026-10-14。基线 feat/2.7.0/beijing-ns-llm-guardrail + 当前本地 Harness 改动。

结论：不支持本次执行点的自动续跑，因而不能满足“任意崩溃自动续跑”。这是反例验证，不是对所有崩溃时间窗口的穷举。

## 测试方式

1. 查询当前独立测试库确认没有运行中任务，避免中断其他任务。
2. 创建无外部写入、无代码工具的计算任务，要求主 Agent 委派成员计算整数平方。任务 8eb1ad46b98a438a91d75bcbefa22a4c，对话 3ebc29029b2d44c0ba2c5c59ac29f999。
3. 观察到真实子 Agent 委派事件，且任务状态为 in_progress 后，使用 taskkill /T /F 强制结束本次本地 Worker 的整个进程树；不是人工澄清等待点。
4. 等待超过 15 秒的旧心跳 TTL，再用相同测试库启动新 Worker。没有重新提交任务、调用继续执行接口或发送人工回答。
5. 查询任务状态、持久化事件和 Redis checkpoint 键，检查启动清扫结果。

## 观察结果

- 重启后原任务变成 failed，原因 Worker node crash detected。
- 新增终态事件为 error_message；原事件历史仍可读取。
- Redis 保留 14 条该任务 checkpoint data 记录，但新 Worker 未从这些记录自动接管。
- 最后一个成员 start 事件为本地 10:04:38.430，失败事件为 10:05:40.250，新调度进程启动于 10:05:48；不应把取快照与强杀之间写入的第二次委派误判为重启后的重放。
- Worker 已恢复正常运行，API 与测试数据保留。没有远端写入。

## 代码对应行为

worker.py 启动时调用 check_and_terminate_incomplete_tasks；domain/utils.py 检查 IN_PROGRESS 任务的 owner 与心跳，失效时将任务标失败，而不是以原 thread_id 调用 LangGraph 接续。

人工等待点 checkpoint 恢复仍是已经通过的另一种能力。实现执行中自动接管还需要：独占租约与失效隔离、执行状态恢复、工具调用幂等/副作用判定、沙箱重连与文件状态恢复，以及相应故障矩阵。本轮仅做用户要求的测试，没有绕过失败保护来伪造续跑成功。

测试未覆盖“新 Worker 在旧心跳过期前启动”、主机断电、Redis/数据库共同故障、外部写入工具执行中崩溃及沙箱丢失，因此不对这些情况声称可自动恢复。

## 后续实现

本文记录自动恢复开发前的失败基线。用户随后确认 crash-recovery-proposal.md，现已完成实现和真实强杀/竞争验证。当前结论及剩余边界见 [automatic-recovery-validation.md](./automatic-recovery-validation.md)，不得把本文历史结果作为新协议的验收状态。
