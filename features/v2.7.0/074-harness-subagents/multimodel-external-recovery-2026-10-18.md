# 多模型组合与外部副作用恢复补测（2026-10-18）

## 结论与范围

两组不同模型配置的主/子 Agent 真实任务通过；外部副作用恢复的五个真实 HTTP + 云端 Redis + 独立进程故障测试通过。本轮没有修改生产实现，没有重启或强杀 API、业务 Worker，没有调用邮件、支付等业务写接口。

模型都是 DeepSeek Flash 系列，不能据此声称跨厂商兼容验证完成。副作用补测直接经过生产 `RecoveryMiddleware`、`ExecutionFence` 和 `validate_receipts`，外部 HTTP 测试账本位于宿主进程，故障子进程终止后仍能独立计数；不是完整的“模型规划 → 已注册连接器 → Worker 崩溃 → 队列调度”端到端测试，也不覆盖所有外部工具协议。此前单次 API 退出原因仍未解决。

## 多模型真实任务

用户新增并启用了模型 18 后，通过已登录本机 API 提交并由现有专用 Worker 执行：

| 主模型 | 子模型 | task | 结果 |
|---|---|---|---|
| 1 / deepseek-v4-flash | 18 / DeepSeek-V4-Flash-0731 | `2ab115a470c746c588d42304cbf4ada2` | completed；成员 mm-18 实际 calculator 返回 3973；子任务 success；主模型返回 MM-1-18-OK |
| 18 / DeepSeek-V4-Flash-0731 | 1 / deepseek-v4-flash | `993c1b369a7e4d2ba7cff64844cec202` | completed；成员 mm-1 实际 calculator 返回 3973；子任务 success；主模型返回 MM-18-1-OK |

任务配置快照分别持久化 main_model_id=1 / child=18 和 main_model_id=18 / child=1。只读核对每个任务的 22 个 Redis checkpoint：子 Agent 模型响应 metadata 分别实际出现 `DeepSeek-V4-Flash-0731` 和 `deepseek-flash`，provider 均为 deepseek。未修改模型凭据、默认模型、启用状态或原常用成员。

## 外部副作用恢复

新增可重复运行的 opt-in 测试 `src/backend/test/linsight/test_external_effect_recovery_live.py`。不设置 `F074_EFFECT_REDIS_URL` 时跳过；配置显式 Redis 地址后，仅创建 UUID 命名的 receipt/lease/count 三个键，收尾按完整键名清理。Redis 凭据只通过进程环境传递，不进入命令行、报告或仓库。

| 故障窗口 | 实测结果 |
|---|---|
| HTTP 接受操作，结果回执已持久化，调用进程立即退出 | 新进程、新 epoch 通过校验并复用原始回执；HTTP 账本计数仍为 1 |
| HTTP 接受操作，尚未写入 done 回执，调用进程立即退出 | 新执行者的恢复校验与同一调用重试都被 RecoveryBlocked 阻断；计数仍为 1 |
| HTTP 接受操作后主动断开响应 | 客户端出现真实 RemoteProtocolError；新执行者双路径阻断；计数仍为 1 |
| HTTP 已返回但旧执行者失去租约 | done 回执写入被 RecoveryLeaseLost 拒绝，保留 started；接管后阻断重放；计数仍为 1 |
| 外部写工具伪装成 read_file | 按 unsafe 处理，不继承内置只读工具重试策略；进程故障后阻断；计数仍为 1 |

这里的“阻断”表示无法确认结果时停止自动重放，需要外部核验与人工处置；不表示撤销了已发生的操作，也不提供任意业务工具的 exactly-once 或自动补偿。

## 检查与证据

- 新增真实副作用 5 项 + 既有 crash recovery 32 项：**37 passed，4 warnings，27.11 秒**。
- configured_subagents / agent_config_service / member_service 回归：**29 passed，4 warnings，0.71 秒**。
- 新增文件 Ruff format/check、arch-guard、git diff --check 通过。
- 收尾本机 API :7860、平台 :3001、客户端 :4001 均 HTTP 200。
- 忽略目录证据：`.venv/f074-evidence/review-1018-multimodel-proof.json`、`multimodel-response-model-proof.json`，以及两组任务的 observed 快照。
- 日志：`.venv/f074-logs/effect-live-recovery-1018.log`、`multimodel-config-regression-1018.log`。原始凭据、启动配置和日志不提交。
