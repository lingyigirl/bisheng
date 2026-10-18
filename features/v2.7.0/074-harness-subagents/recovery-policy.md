# Harness 启用与工具恢复策略

2026-10-18。用户本轮明确要求“无成员也启用 Harness，明确沙箱和外部工具的恢复策略”，本文件记录该范围的规格与设计细化；不扩展为沙箱进程快照或外部工具自动补偿。

## 启用与兼容

- 所有新建任务，包括省略 agent_config 或显式传空成员列表，保存 schema_version=1、event_protocol=2、recovery_protocol=1、实际主模型和空技能摘要。内置研究员与角色预设可用，不自动增加用户未选择的业务工具或自定义成员。
- 显式 `subagents=[]` 表示清空自定义成员；同会话新一轮省略 agent_config 时沿用上一轮成员并重新校验。继承失败沿用现有降级语义，但降级后的新任务仍使用 Harness。
- 前端以 event_protocol 判断是否补播，不能用成员数量判断。历史任务不补写协议、不猜测历史副作用；已完成历史任务保持旧回放方式。
- 无成员代码调用也必须使用 E2B，禁止退回宿主机执行。日常聊天路径不在本次变更范围。

## 崩溃恢复判定

以下适用于 Worker 崩溃后重建图；浏览器断线不重建执行图，不触发工具重放。

| 执行对象与证据 | 恢复动作 | 明确边界 |
|---|---|---|
| 模型推理中断，checkpoint 可用 | 原 task/thread 接续，未完成推理可重算 | 保留完成证据，替换未完成投影，不拼接两次半截答案 |
| 平台内置 ls/glob/grep/read_file，结果未保存 | 可重试 | 必须同时满足内置实现来源与允许名单；外部同名工具不享有此策略 |
| 已保存结果的只读/文件写入/外部 API 或 MCP | 复用原类型化结果，不重新调用 | 参数摘要、调用身份一致；此规则不解除下述沙箱进程状态阻断 |
| write_file/edit_file、外部 API/MCP 已开始但结果未知 | 阻断自动接续，不重发 | 由用户/运维先查远端状态，不能把异常或超时当作“未执行” |
| 任意 Python/E2B 代码已开始或已执行完成 | 阻断本次运行的自动崩溃接续 | 完成回执和同步文件无法证明变量、导入模块、后台进程及连接状态可重建 |
| 未调用代码的任务，仅有持久工作区文件 | 从完整 workspace_epoch 重建文件，再按 checkpoint 接续 | 不读取半完成的复制代次；MinIO 不可用或元数据缺失时明确失败 |
| 后台任务的未完成 job_output 等进程内状态 | 无法证明可重建时阻断 | 原 job registry 不持久化，不把新进程的空 registry 当成子任务已结束 |
| 缺 checkpoint、工具回执缺失/过期、摘要不一致 | 阻断并保留诊断原因 | 不重新提交整项任务冒充续跑 |
| 用户已停止、任务已完成或失败 | 不接管、不复活 | 人工等待点不自动编造答案 |

“新一轮继续”与“崩溃续跑”不同：用户明确发起新一轮后，可以用已同步的文件启动新沙箱；上一轮的 Python 变量和后台进程不会保留。旧轮未同步的文件不能宣称已恢复。

## 外部工具后续可扩展条件

当前外部工具默认不可重放；工具的名称、描述或 HTTP GET 方法都不是安全证明。未来若要允许特定动作自动接续，需要按动作接入可信策略，并分别具备：稳定的幂等键与远端去重约定、按该键查询实际执行状态的接口、明确的结果有效期和租户权限校验，以及“远端已执行但本地未保存”等故障注入证据。需要补偿的动作还必须提供经过授权的补偿流程。本轮未提供这些新执行能力，不声明全局 exactly-once。

## 开销、风险与回退

无成员任务复用现有事件流、工具回执与租约扫描，不新增调度系统或扫描频率；相较旧兼容路径会新增协议要求的 Redis 持久化和租约续期。空成员不查询成员技能与工具目录。无需数据库迁移；回退前应停止接收新协议任务并处理在途任务，避免旧 Worker 消费新协议任务。

## 2026-10-18 实施验证

- 网页取消全部成员后提交任务 `1fdff96e68db409285dfdf4a6b6b5804`（会话 `128fb265c64b48e493c6700e53605220`）：成员列表为空，event_protocol=2、recovery_protocol=1、epoch=1。实际 write_file/read_file 闭环完成，68 条事件；刷新后保留工具历史、文件及 NO-MEMBERS-HARNESS-OK。不是 legacy 响应。
- 无成员崩溃任务 `8161569ba23444ac9837637bdc09ebf9`（会话 `5e014d81ccae4055a58d12441f1bf076`）：首个文件写入回执持久化后，强杀专用测试 Worker 进程树，再分离启动 Worker。原任务从 epoch 1 接续至 2，completed；3438 条事件，旧事件完整保留，1 条 task_recovery。原写入回执保持 epoch 1/done；随后另一个文件写入为 epoch 2 的不同调用。网页实际显示“任务发生中断，已自动接续（第 1 次）”和 NO-MEMBERS-RECOVERED-OK，无自定义成员。
- 历史无成员任务 `dc528fd030834c4db5abd05fad9a7c58` 仍返回 legacy=true，未升级或重跑。
- 后端全量 Linsight：最终 **971 passed、11 skipped**。第一轮 3 项失败来自旧知识库提交测试漏隔离新增模型配置读取，补齐模型/事件依赖桩后全量通过；未放宽生产租户检查。定向配置、提交、恢复与成员装配测试此前 53 passed。
- 前端补播与终止投影：**2 suites / 12 passed**，包含零成员完成任务补播、历史协议不升级、断连重试。全工作区 typecheck、check-i18n 和客户端 lint 通过；根 lint 仍被平台原有 54 项 no-explicit-any 阻断。
- Ruff 对 HEAD 的诊断增量为零；workbench_impl 旧诊断减少 1 项。Architecture Guard 与差异空白检查在提交前运行。
- 当前验证没有新增外部幂等或沙箱进程恢复能力。外部完成结果复用、未知结果阻断、写文件/代码/进程内 job 未完成时不重放由自动化回归锁定；此前真实外部未知结果阻断证据见 automatic-recovery-validation.md，不声称本轮重新执行了所有故障场景。

原始证据保留在忽略目录 `src/backend/.venv/f074-evidence/`：`review-1018-no-members-web.json`、`review-1018-no-members-crash-before.json`、`review-1018-no-members-crash-final.json`、`review-1018-no-members-crash-proof.json`、`review-1018-no-members-historical-legacy.json`、`no-members-ruff-baseline.json`，截图 `no-members-web-replayed.jpg`、`no-members-recovered-web.jpg`。脚本为 `.venv/f074-tools/f074-no-members-crash.py`，测试与运行日志在 `.venv/f074-logs/no-members-*`。不提交凭据或运行配置。
