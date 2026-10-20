# 第二轮网页复测缺陷修复与验证

2026-10-18；对应 `browser-retest-2026-10-18.md` 的三项问题。仅本地修改与提交，不推送远端；记忆服务不在本轮范围。

## 修复

1. **停止后的执行投影**：之前终止事件只改变任务状态，未结束的调用没有停止时间。现在使用持久化 `task_terminated` 的时区明确日期或 Redis stream ID，将仍打开的委派、工具和思考步骤投影为已停止，保留已完成证据与起始帧。实时消息、刷新和历史回放使用同一个纯函数；不会改写原始事件、伪造工具返回或重跑任务。点击停止等待 API 确认后，额外读取一次权威快照，覆盖停止时 WebSocket 已断开的情况。工具行显示“已停止”，计时停止；终态时间和任务状态参与渲染缓存比较。提取所触碰工具行的历史中文文案至三语资源，使用 lint:prune 清理 5 项旧抑制。
2. **无效成员名称**：deepagents 的未知成员错误是普通成功文本，不能仅靠 ToolMessage.status 在后台结算时识别。现在在委派入口以实际构建的子 Agent registry 为允许名单校验，前台与后台均返回 `status=error`，不会调用上游、创建后台 job 或产生完成通知。不依赖错误文本语言或子串。
3. **表格表头**：API `ExcelRule` 默认 `header_start_row=header_end_row=1`，底层 DataFrame 转换器使用从零开始的闭区间。现在在 `BaseFilePipeline` 适配边界统一减一，ExcelLoader 和信创表格路径复用。底层转换器的零基接口及直接调用方保持原语义，不作二次转换。已生成的旧 Markdown 不批量重写；需要重新上传或重新解析才应用修复。

性能：允许名单为每图一次构建的集合；终止投影对既有步骤作线性遍历，仅在终止事件发生时执行。没有新增轮询、模型调用或数据库写放大。停止动作增加一次快照请求。无需迁移，相关本地提交可独立回退。

## 真实网页与服务证据

| 场景 | 实测结果 |
|---|---|
| 原缺陷任务 `c4972f2ec9ce4ae4b9057dacf7a6a859` | 不修改历史事件，重新打开页面即可显示“已停止 · 31s”，待收集的工具显示“已停止 job_output”；原来为 1s 且仍正在调用 |
| 新任务 `9a527e6cb33a427eb44c5ed97ca04fd7` | 模型先请求 `member-lib-11: invalid-name`，事件记录明确为 outcome=error；随后改为正确成员，真实云端执行 sleep(600)，主 Agent 用 job_output 等待 |
| 网页停止新任务 | 点击 Stop generating，显示“已停止 · 25s”和“已停止 job_output”；刷新仍为 25s。服务端 terminated、最后事件 task_terminated、314 条事件、output_result=null |
| 云端回收 | Worker 日志记录两个 E2B sandbox DELETE 204；停止后不存在最终产物，无需等待 600 秒 |
| 网页上传与只读核验 `5bbbcaf1e0b34d019289b6d074a0a4b9` | 网页上传相同 5 行的 CSV 和 XLSX，主 Agent 和 lib-9 独立核验员分别读取两个 `.md`；4 次实际 read_file 返回均以 `date / amount` 为表头，首数据为 `2026-03-02 / -20` |
| 表格最终结果 | 网页显示 FIX2-HEADERS-OK；5 行，1 月 10、2 月 -10、3 月 100，总计 100。读取事件没有访问原始 CSV/XLSX，也没有代码执行，排除依靠原件绕过错误 Markdown 的情况 |

原始 JSON、日志、截图放在忽略目录 `src/backend/.venv/f074-evidence/` 和 `f074-logs/`：

- `review-1018-fix2-stop-running.json`、`review-1018-fix2-stop-final-snapshot.json`、`review-1018-fix2-headers.json`。
- `fix2-old-stop-replay.jpg`、`fix2-live-stop.jpg`、`fix2-stop-reloaded.jpg`、`fix2-headers-completed.jpg`。
- API `f074-api-082248.out.log`；Worker `f074-worker-082231.out.log`。

## 自动化与门禁

- 后端定向：21 passed。包含无效成员前台/后台拒绝、正确成员执行、后台生命周期、CSV/XLSX 默认/多行/自定义表头。
- 后端 Linsight 与表格邻域回归：970 passed、11 skipped，日志 `fix2-linsight.log`。
- 前端最终回归：4 suites、100 passed，覆盖终止投影的时长、幂等、已完成证据、嵌套历史、时区、恢复投影、执行树和卡片；`fix2-client-final.log`。
- 根目录 typecheck、check-i18n 通过；最终客户端 lint 与 typecheck 通过。根目录 lint 仍在未修改的 platform 处被 54 项既有 no-explicit-any 阻断，与本轮前基线一致，不声称全工作区 lint clean。
- Ruff 与 HEAD 按诊断代码/内容比较：零新增；agent_factory 原 428 项、background_subagents 原 47 项仍在，其余修改 Python 文件零诊断。证据 `fix2-ruff-baseline.json`。
- Architecture Guard 逐个检查修改/新增文件，无报告；git diff --check 通过。

这份记录确认本轮三项缺陷的修复，不替代 DM8、信创浏览器及长期记忆等既有范围边界的验收。
