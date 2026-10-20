# Harness 本地审查与真实验收 — 2026-10-18

## 结论

**NEEDS_FIX：核心执行链路可用，配置连续性与恢复安全存在阻断问题。**

本轮按用户要求启动当前本地源码，结合浏览器、真实模型、E2B、已注册连接器、Redis 事件和下载文件验收。会话记忆、长期记忆不纳入本轮实现与验收。没有修改业务代码、提交或推送代码。

结论区分三类证据：真实环境复现、隔离故障模拟、代码调用链确认。此前报告仅作为环境线索，不计入本轮通过项。

## 环境与范围

| 项目 | 当前状态 |
|---|---|
| 分支 / HEAD | `feat/2.7.0/beijing-ns-llm-guardrail` / `f1b49c51f`，审查重点为工作区本地增量 |
| API | `http://127.0.0.1:7860`，`/health` 成功 |
| Platform / Client | `http://127.0.0.1:3001` / `http://127.0.0.1:4001/workspace` |
| Worker | 当前源码独立 Linsight Worker，1 个调度进程，最大并发 2；故障测试后已重新启动 |
| 中间件 | 复用独立 Docker 项目 `jinbao-f074`：MySQL、Redis、OpenFGA、MinIO、ES、Milvus、etcd |
| 数据库 | 独立测试库 `bisheng_f074_rebase`；当前唯一迁移 head 为 `f088_agent_member_default` |
| 浏览器认证 | 用户正常完成验证码登录；API 探针复用该登录，没有注入浏览器登录态或改写验证码 |
| 模型 | 页面 `model 1`、`model 2`，本环境实际模型 ID 分别为 `3`、`4` |
| 证据 | `src/backend/.venv/f074-evidence/review-1018-*`；脚本与日志在相邻 `f074-tools/`、`f074-logs/`，均为本地忽略文件 |

## 对照目标的实际验证

| 能力 | 本轮结果 | 证据与边界 |
|---|---|---|
| 按目标规划、推理、工具调用、结果回传、调整与收尾 | 通过基本链路 | 页面提交月度汇总任务；主 Agent 建立 4 项计划，检查工作区，委派计算，读回产物，委派核验，写报告并完成 |
| 云端沙箱 | 通过 | 子 Agent 真实调用两次 Python；工具返回 `platform.system()=Linux`、`cwd=/home/user`，宿主为 Windows |
| 隔离执行环境 | 通过文件隔离专项 | 创建两个真实 E2B sandbox，ID 不同；A 写入 `scratch/isolated-1018.txt`，B 检查返回 `A_FILE_EXISTS=False`；结束后关闭这两个专项沙箱。这不是完整容器逃逸或网络安全审计 |
| 子 Agent 名称、提示词、模型、Skills、工具独立配置 | 首轮通过，续轮失败 | 页面新建计算员与核验员；计算员使用模型 3 / xlsx / 代码执行器，核验员使用模型 4 / 无额外工具。下一轮被默认成员替换，见 F-01 |
| 主 Agent 路由、收集并整合结果 | 首轮通过 | 两位配置成员的 invocation 身份和工具过程可区分；核验员实际读取计算员 CSV；主 Agent 汇总 200、200、400 |
| 工具注册调用、Skill 与连接器 | API 真实任务通过 | 使用已有注册动作 ID 39 和 `e2e-f074-multiply` Skill；子 Agent 读取 Skill 后真实调用 `83×97`，返回 `8051`；测试 HTTP 服务记录一次请求及独立标记。没有把该项计为浏览器连接器选择交互通过 |
| 长时任务流式反馈 | 通过 | 页面持续收到执行步骤、成员状态和最终结果；首轮保留 1,427 条事件；崩溃专项也有实际流式生成 |
| 断线重连、游标补播 | 通过协议专项 | 真实 WebSocket 读取 10 条后主动断开，再携最后游标重连；逐条对照完整事件快照，1,426 条 ID 顺序一致、重复 0、遗漏 0。`after=1-0` 返回 `replay_reset` |
| 浏览器重载恢复展示 | 通过基本链路 | 真实页面刷新后恢复两成员执行过程、最终答复、文件；崩溃专项在运行中刷新并恢复到完成态。没有把浏览器刷新或协议断连描述为物理网络中断测试 |
| 断点续跑 | 受限通过 | 主 Agent 已持久化文件后强杀 Worker 进程树；租约失效后新 Worker 接管 epoch 1→2；原事件前缀完整保留，结束标记与文件均存在。研究员恢复保护缺失，见 F-02；不能据此承诺任意工具/沙箱状态可恢复 |
| 文件交付 | 首轮通过；恢复后清单有回归 | 浏览器下载 CSV，实际字节为两月各 200；恢复文件下载内容为 `RESUME-1018`。恢复后上一轮未修改文件再次列入本轮清单，见 F-05 |
| 同一 session_version 续聊 | 失败 | `/workbench/continue` 返回成功，却丢失新问题，最终仍为旧答复，见 F-03 |
| 记忆 | 排除 | 按本轮用户要求，不实现、不计通过、不作为阻断 |

## 问题清单

### F-01 [P1] 默认成员覆盖已提交配置，并污染下一轮

**真实浏览器与接口复现。**

1. 新建任务，取消默认“财务分析师”，选择本轮新建的“e2e-1018-计算员”和“e2e-1018-核验员”。
2. 首轮真实提交保存了 `lib-6`、`lib-7`，并按两位成员执行成功。
3. 提交后输入框重新挂载，默认成员先填入草稿；任务快照随后到达时，非空 `memberDraft` 阻止其覆盖默认值。
4. 输入框只读抽屉显示“财务分析师”，同时宣称“本轮配置已锁定，继续任务时沿用此配置”。
5. 不改成员，提交“沿用上一轮计算员和核验员”的追问。新任务保存的配置实际变为 `lib-2 / 财务分析师 / model 3 / xlsx / 无可选工具`，不再是原两名成员。

位置：`src/frontend/client/src/components/Chat/AiChatInput.tsx:157`、`:172`、`:203`，以及两处没有会话隔离 key 的 `ChatView` 输入框。

影响：页面展示、冻结快照和实际执行不一致；用户未经选择就失去之前的独立模型、提示词与工具组合。上一轮审查中的“切换会话草稿串用”风险本轮进一步复现为正常首轮提交后的配置污染。

建议：草稿显式绑定会话/轮次身份；区分初始化、用户编辑和服务端锁定快照；服务端锁定配置不能被默认成员抢先占位。回归必须覆盖缓存先命中默认库、快照后到达、初次提交输入框换位和既有会话切换。

证据：`review-1018-wrong-locked-member.jpg`、`review-1018-primary-snapshot.json`、`review-1018-followup-versions.json`、`review-1018-findings-proof.json`。

### F-02 [P1] 内置研究员缺少恢复回执，代码调用结果未知仍自动接管

**代码确认 + 真实进程崩溃观察。**

`agent_factory.py:934` 为 `general-purpose` 单独装配中间件，缺少 `RecoveryMiddleware`；后续 `child_middleware()` 仅用于配置成员和其它内置角色。已安装 deepagents 的子图装配代码确认：子图只加入自身 spec 的 middleware，不继承主图 middleware。

真实续轮 `19a66a0a65ba43e2bfa409c9038ef238` 中，研究员在 epoch 1 调用代码解释器：一次有完成结果，另一次有 start、没有 end。强杀 Worker 后它仍被接管到 epoch 2，又执行了代码调用。Redis 工具回执中 `bisheng_code_interpreter` 数量为 **0**，未触发 `validate_receipts()` 的沙箱状态/未知工具结果阻断。

真实证据只证明“未知执行窗口未受保护、恢复后继续执行代码”，不宣称已观察到某个外部写入实际重复；外部副作用重复属于有调用链依据的风险。

影响：已承诺的回执安全规则在研究员路径失效。主 Agent 默认可以委派研究员，因此这不是只在特殊配置下出现的旁路。

建议：所有可执行工具的子图使用同一恢复安全栈，重点覆盖 general-purpose 的代码执行、外部连接器、租约丢失以及未知结果强杀窗口。

证据：`review-1018-followup-snapshot.json`、`review-1018-researcher-receipts.json`、`review-1018-findings-proof.json`。

### F-03 [P1] 同版本续聊返回成功，但新问题被丢弃并误恢复旧结果

**真实 API 复现。**

1. 等待连接器任务 `29214512af3847979fd5962f282c3649` 完成。
2. 调用 `/workbench/continue`，新问题要求不使用工具、仅回复 `CONTINUE-1018-OK`。接口返回 `data=true`。
3. 端点先将状态改为 `IN_PROGRESS`；普通续聊项进入 `RecoveryService.run()` 后，`RecoveryRepository.claim(recovering=False)` 在 `recovery_repository.py:45` 直接返回 None。
4. 扫描器随后把任务当成待恢复任务，将 epoch 升到 2；它读取旧 checkpoint 并再次完成旧答复。
5. 最终答复仍是 `8051` 和上一轮连接器结果，新要求的标记不存在。

影响：用户收到成功确认，却得不到本轮请求的处理；终态还会伪装成成功。旧全页任务路径仍通过 `useLinsightManager.continueConversation()` 调用该接口。

建议：统一续聊状态机与认领语义；持久化本轮输入，区分 fresh / resume / continue / recover，确保扫描接管不会把已接受的新输入丢掉。

证据：`review-1018-continue-response.json`、`review-1018-connector-before.json`、`review-1018-connector-after-continue-snapshot.json`。

### F-04 [P1] E2B 重建重试覆盖执行后阶段，可能重复执行代码

**隔离故障模拟复现，未在云端故意制造外部副作用。**

位置：`src/backend/bisheng_langchain/gpts/tools/code_interpreter/e2b_executor.py:175`—`182`。重试捕获的是完整 `_run_once()`；其中 `run_code()` 之后仍有结果解析、本地同步、copy-out。

模拟先让 `run_code()` 返回成功，再让第一次 copy-out 报告 sandbox not found，真实 `run()` 的重建逻辑会第二次调用 `run_code()`。探针断言执行计数为 **2**。

影响：对外 HTTP 写入、数据库更新或其它非幂等操作即使已经成功，也可能随“沙箱重建”再次执行。源码注释“总是在 run_code 前发现死沙箱”不覆盖执行中到期、执行后到期和返回结果未知的窗口。

建议：仅对明确发生在代码启动前的初始化/同步失败自动重试；执行开始后结果未知应明确失败或交由显式幂等协议处理，不可整段重放。

### F-05 [P2] Worker 恢复后遗失继承文件基线，旧产物被重复当成本轮输出

**真实续轮恢复复现 + 文件摘要比对。**

本轮追问只要求新增 `review-1018-monthly-v2.csv`；恢复后新任务还将未修改的 `review-1018-monthly.csv` 和 `review-1018-report.md` 再次列入自己的 `final_files`。它们与上一轮同名文件的 `file_md5` 完全相同。

位置：`task_exec.py:206`、`:877`、`:1022`。继承文件排除依赖进程内 `_synced_output_md5`；它仅由首次 `_sync_workspace_originals()` 填充。新进程走恢复路径时重新复制 workspace，却没有重建该基线，最终扫描重新收集全部 output 文件。

影响：本轮交付清单混入旧报告和旧 CSV，用户可能选错版本；与跨轮工作区本来就可访问旧文件是两个不同问题。

建议：将继承基线作为轮次元数据持久化，或在恢复 workspace 时恢复同一来源清单/内容摘要；恢复前后新产物集合应保持一致。

证据：`review-1018-primary-snapshot.json`、`review-1018-followup-snapshot.json`、`review-1018-findings-proof.json`。

## 实测任务索引

| 场景 | session_version_id | chat_id / 页面 |
|---|---|---|
| 页面双成员 + E2B + 下载 | `68691fd9effb42518ec47cc3da615a0b` | `7dee3f07179f41f594678256c0cc0ede` |
| 同会话追问、错误成员与恢复安全观察 | `19a66a0a65ba43e2bfa409c9038ef238` | 同上 |
| 注册连接器 + 同版本续聊失败 | `29214512af3847979fd5962f282c3649` | `e2e4e6a7422e4b0299249bb64f82d42e` |
| 初次恢复探针（结束过快，未注入故障） | `c0cecfc03f7e43debc77106840d8a26d` | `fc0a1e5fb2284487bc90cc066d49ca56`；不计崩溃覆盖 |
| 已持久化文件后强杀 Worker | `a5831e4d781648c4ad384b2d863c5d38` | `809bd74e566549aaa23a6be90c076e0e` |

崩溃测试杀掉的是共享本地测试 Worker 进程树，因此同时中断了本轮另一条仍运行的追问任务；两者均为本轮创建的测试任务。独立主 Agent 恢复专项证明受控恢复成功，追问任务揭示研究员保护缺口，二者不混为同一个通过结论。

## 测试与未覆盖项

- 审查阶段：选取后端 47 passed，前端 5 suites / 25 passed。
- 前端根目录 typecheck 通过；client lint 通过。
- 前端根目录 lint 在 platform 阶段遇到 54 个 `no-explicit-any` 错误（dashboard.ts / finetune.ts / workflow.ts），未据此声称全量门禁通过。
- 完整 `test/linsight`：**938 passed / 8 skipped / 0 failed**，耗时 131.57 秒；日志为 `.venv/f074-logs/review-1018-backend-tests.log`。跳过的真实联调用例需要显式环境变量，本轮上面的独立真实探针另计。
- 收尾检查：本轮任务均已终态，Redis 活跃执行租约为 0；API 健康检查 200，匿名访问成员库 401；API、Worker、两个前端保持运行。
- 未执行数小时压力测试、真实物理断网、全平台 DM8 回归、任意外部写入的恰好一次验证、完整沙箱安全审计。
- 未新增/实现会话记忆和长期记忆。

## 建议修复顺序

1. F-02 / F-04：先补恢复与重试安全边界。
2. F-03：统一同版本续聊输入与恢复状态机，防止请求静默丢失。
3. F-01：修复默认草稿与锁定配置的优先级、会话隔离。
4. F-05：持久化产物来源与继承基线。

修复后复跑本报告的相同故障窗口和浏览器续轮流程；不能仅以成功完成一条简单任务作为完整验收。

## 修复与复验（2026-10-18）

用户授权修复后，F-01 至 F-05 均已实施修正，保留在本地工作区，未提交远端。

| 问题 | 修复 | 回归证据 |
|---|---|---|
| F-01 | 提取 `useMemberDraft`，草稿按 conversationId 隔离；服务端快照优先于缓存默认成员；空快照同样生效；默认成员仅用于新会话；保留显式取消选择 | Hook 行为测试覆盖迟到快照、空快照、会话切换及取消选择；浏览器续聊后后端锁定成员仍为 `lib-7` 核验员，未混入默认财务分析师 |
| F-02 | researcher、配置成员和内置角色统一使用 child middleware 工厂，恢复协议下均装配 RecoveryMiddleware | 工厂测试检查主 Agent 与每个子 Agent；真实 researcher 写入产生 durable receipt，崩溃后回执仍为 epoch 1 的单次完成记录 |
| F-03 | 行锁事务持久化续聊 question/request id 后再入队；正常 worker 和恢复扫描都读取同一持久化输入；固定 HumanMessage id 判断已入检查点，恢复时不重复追加；续聊恢复复用投影恢复与人工输入处理 | 同版本 `/continue` 实测返回 `CONTINUE-FIX-1018-OK`；真实 LangGraph 测试覆盖输入前、输入已持久化但执行未完成、已完成三个窗口；调度测试覆盖缺失队列 question；新轮次不要求重建先前已结束轮次的沙箱进程，但当前轮次仍执行严格回执验证 |
| F-04 | 重建沙箱的重试仅包围执行前准备；`run_code` 开始后，执行、解析或 copy-out 异常均直接抛出 | 三个故障点均断言代码最多调用一次、不重新创建沙箱；原有执行前过期沙箱单次重建测试通过 |
| F-05 | 将继承 output 的相对路径与 MD5 写入 recovery_state；新 attempt 从同一基线重建本地过滤映射 | 单测覆盖恢复目录变化、未改旧文件排除、改写旧文件重新交付；真实崩溃恢复后 `final_files` 仅有 `fix-new.txt` |

### 真实故障复验

- 基础轮次：`e9e0261678d745fbbed59ae73c916602`，创建 `output/fix-inherited.txt`，内容为 `OLD-OUTPUT-FIX-1018`。
- 浏览器续聊：`93ea1239eba24851876912765424ed19`，会话 `607f41a97a81470cac18bae5946aa600`。继承旧文件，主 Agent 调度 researcher 读取旧文件并写入 `output/fix-new.txt`。
- 在 researcher 的 `write_file` 回执变为 done、任务仍在执行时强杀本地测试 Worker 进程树，随后通过原 WMI 入口重启。
- 最终任务 **completed / epoch 2**，保留全部故障前 event_id；写入回执仍为 **epoch 1 / done**；交付清单仅包含新文件，旧文件内容保持不变；回答包含 `RECOVERY-FIX-1018-OK`。
- 浏览器可见自动接续提示、正确的核验员成员、新文件入口；打开文件确认内容为 `NEW-OUTPUT-FIX-1018`。
- 证据位于 `src/backend/.venv/f074-evidence/`：`review-1018-fix-continue-snapshot.json`、`review-1018-fix-recovery-before.json`、`review-1018-fix-recovery-after.json`、`review-1018-fix-recovery-proof.json`、`fix-1018-browser-complete.jpg`。

### 最终检查

- 后端完整 `test/linsight`：**954 passed / 8 skipped / 0 failed**，126.76 秒；`src/backend/.venv/f074-logs/fix-1018-backend-final.log`。Windows 首次未启用 UTF-8 的测试出现 3 个编码失败，统一 `PYTHONUTF8=1` 后完整复跑通过。
- 前端相关测试：**5 suites / 26 passed**；client lint、platform 与 client typecheck、根目录 check-i18n 通过。
- 后端修改文件已执行 ruff format/check；既有中文标点告警，以及原有 17 项非 RUF001 告警仍存在，未新增同类违规。新建回归测试和 recovery service/repository/middleware 文件的检查通过。
- Architecture guard 无 VIOLATION；原 endpoint 的数据库模型导入仍有 RULE-3 迁移期 WARNING。根目录 frontend lint 的 platform 54 项历史错误见上方审查记录。
- API、Worker 已加载最终代码，前端通过 Vite HMR 更新；健康检查 200，活跃执行 lease 为 0，未发现积压任务队列。
- 沿用上述未覆盖项；本轮未实现记忆功能。无法重建的沙箱进程状态或结果不明的外部副作用会明确阻止自动重放；本次修复不代表任意外部副作用具有恰好一次保证。
