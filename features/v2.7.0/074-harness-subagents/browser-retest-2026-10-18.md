# 第二轮独立网页复测（2026-10-18）

基于 `2e27892e7` 的已提交代码，重新执行自动化及真实浏览器测试，没有修改产品代码。核心链路再次通过，但异常状态展示仍有 2 项残留，另确认 1 项既有文件转换问题；不能认定所有异常场景已经完整。

## 已验证

| 场景 | 本轮实际结果 |
| --- | --- |
| 独立边界数据 | 网页上传中文名 `复测-1018-含负数.csv`，带 UTF-8 BOM，5 条乱序记录含负值和 0。真实云端 Python 得到 1 月 10、2 月 -10、3 月 100，总额 100 元；未沿用之前的 400 元样本 |
| 并行、独立模型、Skills、连接器 | 两个配置成员并行，计算成员读取 Skill、运行 Python，真实连接器以 a=100,b=3 返回 300 与验证标记；只读核验员独立手算后再次核对实际 CSV |
| 后台生命周期 | 成功卡片显示实际最终报告，耗时为 120s、17s、14s；刷新后保持，无提前按启动回执宣告完成 |
| 文件下载 | 网页下载 `retest-1018-monthly.csv`，用 CSV 解析器确认三行分别为 10、-10、100，月份升序 |
| 等待成本 | 边界任务主 Agent 只调用 job_output 2 次，没有主 Agent 代码执行或沙箱 sleep 空等；子成员按验收要求 sleep(30) 模拟计算 |
| API 中断自动恢复 | 08:05:41 停止 API，08:06:08 分离重启；网页实际出现全屏故障提示，之后自动消失，未点击刷新。Worker 不停，慢任务正常完成，epoch 保持 1 |
| 超时后继续收集 | 上述慢任务真实 sleep(180)，首次 job_output 等待 120 秒后返回运行中，第二次收集到真实 START/END 与产物。等待超时没有误杀子任务 |
| WebSocket 断点补播 | 主动断开再从游标接续，核对 1835 条事件，重复 0、遗漏 0；无效游标按预期 replay_reset |
| 同会话追问 | 刷新后无工具、无委派追问，正确回答 10/-10/100、总额 100、乘三 300、记录 5 条，标记 RETEST-1018-CONTEXT-OK，无新产物 |
| 空成员兼容 | 取消全部自定义成员，仍能调用内置 general-purpose；代码调用运行中从网页停止，状态 terminated。该配置走旧兼容路径，replay-snapshot 返回 legacy，不将其算作新版恢复协议通过 |
| 新版 Harness 运行中取消 | 单独配置慢任务成员，子 Agent 的代码调用处于未完成状态时点击停止；服务端 terminated、无最终产物。Worker 08:09:45/46 向主/子两个 E2B 沙箱发 DELETE，均返回 204；后续事件数维持 294，没有执行结束后再交付文件 |

180 秒任务正常完成后才尝试点击停止，因此那次不计为取消通过。随后另起 sleep(600) 的测试，在实际运行中成功停止，以下终止相关问题均来自后一个任务。

## 残留问题

### P2：终止后部分工具仍显示运行中，子 Agent 耗时回退

复现版本 `c4972f2ec9ce4ae4b9057dacf7a6a859`：停止前网页显示“正在执行 · 31s”；停止后显示“已停止 · 1s”，工具记录的 DOM 中 job_output 行仍为“正在调用 job_output”，源码也仍按 running 选择运行图标。刷新后仍可复现，尽管任务已 terminated、停止按钮消失、云端沙箱已删除。截图可直接核对耗时回退；折叠工具区中的状态另由 DOM 与 API 快照佐证。

证据：`retest-1018-stop600-running.jpg`、`retest-1018-stop600-replay-issue.jpg`；API 快照中真实委派、job_output、代码调用均缺少对应 end，294 条事件的后续复查不再增长。

定位：`stream_event_mapper.py:283` 的 terminate 不为未完成调用生成终止投影；`ToolRowLite.tsx:55,83,91` 只依据 step.running 显示运行，不考虑回合终止；`SubagentRunCard.tsx:24` 在回合结束后以最后一条活动帧的 endedAt 计算耗时，而非停止时间。

建议：为终止的未完成调用统一记录终态和停止时间，并在回放中使用该投影。不要只在前端隐藏 spinner，否则历史耗时和状态仍错误。

### P2：成员类型错误的后台任务被标为 completed

同一取消测试中，模型首次传入 `subagent_type="member-lib-11: e2e-retest-1018-慢任务"`，运行时明确返回 `We cannot invoke subagent ... because it does not exist`。但 job_1 结算事件的 job_status 是 completed、outcome 是 success，完成通知让主 Agent 将其描述为“任务已完成（可能被人工停止）”。随后主 Agent 读取错误、改用正确的 `member-lib-11`，才启动实际子任务。

定位：`background_subagents.py:210` 仅检查 ToolMessage.status；上游对未知成员类型返回了普通成功 ToolMessage 的错误文本，因此未被识别为失败。这是状态分类缺陷；首次错误参数来自模型，本轮没有把模型错误本身算作平台取消失败。

建议：委派入口按真实可用成员类型做结构化校验，或由上游明确提供错误状态；不要通过语言关键字猜测普通报告是否失败。测试应包含未知成员类型与主 Agent 纠正后再次调用。

证据：`review-1018-retest-stop600-before-stop.json` 中错误委派的最终事件；Worker 日志显示 job_1 几乎立即 settled: completed。

### P2（既有）：表格 Markdown 视图将首条数据当作表头

同一文件通过 read_file 读取原始 CSV 是：

```text
date,amount
2026-03-02,-20
2026-01-05,0
...
```

读取其解析 Markdown 却是：

```text
| 2026-03-02 | -20 |
|---|---|
| date | amount |
| 2026-01-05 | 0 |
...
```

表头与首条数据的结构位置错误；上轮 XLSX/CSV 场景也出现过。本轮 Agent 通过读取原始 CSV 与真实代码计算规避，最终交付正确，但仅依赖 Markdown 的消费者可能误判表头或漏算第一行。

证据：`review-1018-retest-edge-final.json` 的两次 read_file 实际输出，原始 fixture 和下载验证。需继续排查上传解析的表头参数与 `knowledge/rag/pipeline/loader/utils/md_from_excel.py` 之间的索引约定；本轮不将该定位推断写成已确认根因，也没有修改解析器。

## 自动化与收尾

- 后端 `test/linsight` 重新执行：**959 passed / 11 skipped / 0 failed**，158.39 秒。
- 前端同一组输入、成员、遮罩、执行卡片和 WebSocket 测试重新执行：**8 suites / 38 passed**。
- 本轮无产品代码变更，未重复运行类型/i18n/lint；上轮已通过的客户端检查及平台历史 54 项 lint 错误保持原记录，不将它们算作本轮新执行。
- 记忆、DM8、数小时长稳、完整安全隔离矩阵与 Worker 任意崩溃点不在本轮覆盖范围。
- 仅新增测试成员 `e2e-retest-1018-慢任务`（库 id 11，非默认，只选代码工具），保留测试任务和构造数据以便复现；没有删除既有业务数据。
- 收尾 API :7860、平台 :3001、客户端 :4001/workspace/ 均返回 200；两项完整任务与追问 completed，取消任务 terminated 且事件数仍为 294。保留边界任务浏览器页面，关闭辅助取消测试标签。

## 证据索引

| 场景 | chat / session version |
| --- | --- |
| 边界数据任务 | `6a879e44ecf84682aa0e5be84bfe1226` / `0b9ff5cf27e749ed935b860d8d4937ca` |
| 无工具追问 | 同一 chat / `0bcb6c35c66a414d8cf8f83cdb7d230b` |
| 空成员兼容取消 | `f1f3d9886aea48bbb9fce5b50b45d1ca` / `dc528fd030834c4db5abd05fad9a7c58` |
| API 中断、长等待后完成 | `500ab7229ac44aac873bd94240bd242a` / `36e92baf5b13418a9c44541111131789` |
| 新版运行中取消 | `78a387bba2a64312bd14f76a99fe3702` / `c4972f2ec9ce4ae4b9057dacf7a6a859` |

临时快照/截图位于 `src/backend/.venv/f074-evidence/review-1018-retest-*` 与 `retest-1018-*.jpg`；检查日志为 `src/backend/.venv/f074-logs/retest-1018-*`，Worker 日志为 `f074-worker-074354.out.log`。凭据、配置、日志、截图及产物均留在本地忽略目录，不进入 Git。
