# 无成员 Harness 独立复测

2026-10-18，测试基线 `648ceac7a`。本轮没有修改产品代码，使用新任务和新数据重新验证；本记录不替代长稳、灾备和全平台兼容验收。

## 结果

| 场景 | 新任务/会话 | 实测结果 |
|---|---|---|
| 网页取消所有成员后提交 | task `8581a58d566448ecb3b8bc555a2942ce`；chat `2f35e9e90bd04a1dab22a9d1e7c5e437` | subagents=[]，event_protocol=2、recovery_protocol=1。实际 write_file/read_file 完成，CSV 含 -7、0、19、22；网页下载后独立 CSV 解析确认 4 行、合计 34。刷新后工具历史、结果与产物保留 |
| 有成员执行回归 | task `3d95490dd3704b8f95f2cdd1624a8738`；chat `bc9d55369a8a4045aecea5a0f825dd48` | 主 Agent 创建 CSV，lib-9 独立只读核验后返回 4 行、34，任务 completed；未因默认 Harness 改动丢失委派 |
| 同会话显式清空成员 | task `e5eb9de10c924dfc94425e7ac49ad670`；沿用上一行 chat | 新轮 subagents=[]；只调用 read_file 读取上轮真实文件，无子 Agent 调用。返回 4 行、34，网页出现 RETEST-CLEAR-MEMBERS-OK；上一轮成员卡仍可查看 |
| 真正断开 WebSocket 后补播 | 第一行无成员任务 | 分两次连接，从游标继续读取；核对 90 条事件，重复 0、遗漏 0；无效游标返回 replay_reset |
| 省略 agent_config 后崩溃接续 | task `cb68c29200df4d0fbb9f08b5bf6d1e9a`；chat `fe36ed3e7072468eb5c6d4b072c32c6e` | 请求确实不含 agent_config。文件结果持久化后强杀专用 Worker 进程树，再启动 Worker；原 task 从 epoch 1 接续到 2/completed，成员列表仍空，旧事件完整保留，1 条 task_recovery。网页显示“已自动接续（第 1 次）”及 RETEST-MEMBERLESS-RECOVERED-OK |
| 历史任务兼容 | `dc528fd030834c4db5abd05fad9a7c58` | 仍为 legacy=true，没有补写新协议或重跑 |

本轮范围内未发现新缺陷。实际故障注入仅针对专用本地测试 Worker；重启后 API :7860、平台 :3001、客户端 :4001/workspace/ 健康请求均为 200。原浏览器页面保留，临时测试标签关闭。

## 自动化

- 后端全量 `test/linsight`：971 passed、11 skipped，156.66 秒。包含外部完成结果复用、未知外部结果阻断、内置只读与外部同名区分、沙箱状态不可重建时阻断、终止与旧代次隔离等恢复策略回归。
- 前端 completedReplay、terminationProjection、recoveryProjection、SubagentRunCard、SubagentPicker：5 suites、33 passed。
- 本轮未修改产品代码，未重复 lint/typecheck/i18n；不将上轮检查冒充本轮重跑。平台既有 54 项 lint 错误仍是此前记录的限制。
- 本轮没有重新对真实 E2B 或外部 API 注入未知副作用故障；这两类规则本轮重新执行了自动化测试，真实服务证据沿用 recovery-policy.md 与 automatic-recovery-validation.md。

## 证据

原始记录位于本地忽略目录 `src/backend/.venv/`，没有提交凭据或日志：

- `f074-evidence/review-1018-memberless-retest-{web-snapshot,member-snapshot,clear-snapshot,download,reconnect-proof,crash-before,crash-final,crash-proof,legacy,health}.json`。
- 截图 `f074-evidence/memberless-retest-web-refresh.jpg`、`memberless-retest-recovered-web.jpg`。
- 日志 `f074-logs/memberless-retest-backend.log`、`memberless-retest-client.log`、`memberless-retest-crash-live.log`；本轮恢复后的 Worker 日志 `f074-worker-090400.out.log`。
- 独立脚本 `f074-tools/f074-memberless-retest-crash.py`；本轮下载文件 `C:/Users/hei/Downloads/retest-values.csv`。
