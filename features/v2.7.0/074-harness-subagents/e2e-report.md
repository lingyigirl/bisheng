# F074 E2E 验证报告

> 最新状态：2026-10-14 已完成新基线真实 E2B 文件分析/下载、双成员、补播、人工等待点重启恢复及撤权复测。152 项后端回归、5 项真实环境测试通过。以 [current-acceptance.md](./current-acceptance.md) 为准；下文早期环境限制保留为历史记录。

**状态：PARTIAL。真实模型、Docker 中间件、API/Worker、工作空间核心流程、浏览器连接实际中断恢复和 Worker 崩溃终态通过；E2B、DM8 与部分资源/权限场景尚未完成。**

## 2026-10-13 真实断连与故障注入续测

用户已明确 E2B 尚未配置，先完成其他验证。本轮未用本机代码执行冒充云沙箱。

### API 中断与浏览器自动重连

- 复现旧问题：主动结束 API 进程树，页面仅每秒重试 3 次，API 恢复后仍不连接。
- 修复：运行中任务采用 1/2/4/8/16/30 秒（上限 30 秒）持续退避重试；初次快照请求失败也重试；成功连接清零计数；结束、终止、离开任务或卸载时清理定时器和连接。online 事件可提前触发恢复。
- 真实复测：任务 `6bead64b07ec4833b3057a0bf975649d`，页面已接入人工等待状态；20:33:51 结束 API，停止超过 40 秒后重启。浏览器日志显示退避重试持续至 20:35:22 自动连接成功，全程未刷新页面。随后确认继续，子 Agent 返回 47×53=2491，页面自行到达完成态。
- 这是真实 API 连接中断与恢复，不是前一轮未生效的 CDP offline 模拟；不将其扩大为所有代理/移动网络环境都已验证。

### Worker 强制结束与主动终止

- 在真实模型已委派子 Agent 后，强制结束整个 Worker 进程树；没有选用外部写入工具。旧逻辑在新 Worker 启动清扫时只将 DB 标记 failed，页面仍显示正在执行。
- 修复：启动清扫确认孤儿任务、写入失败状态后，向该任务所属租户的 Harness Stream 追加幂等 `error_message`（producer_id=worker-crash）。不重新入队、不执行工具；旧任务不改变事件协议，存活 Worker 的任务不发送失败事件。
- 复测任务 `4c701ea30f534cef8259a20a9af7cb31`：旧心跳过期后启动新 Worker，DB 为 failed，Stream 最后为 error_message；原页面无需刷新显示错误，两个同名计算成员的不同 invocation 均显示已停止，目标分别保持 101–150、151–200，没有新委派。
- 主动终止任务 `3b1d106df12143eb83e1520aca0ebee1`：真实委派开始后调用 terminate-execute，DB 为 terminated，Stream 最后为 task_terminated，页面两个调用均为已停止，待办显示任务已终止。
- 边界：崩溃检测沿用**新 Worker 启动时、旧心跳已过期后的清扫**，并非持续故障探测器；没有实现任意执行点自动安全续跑。若立即重启时旧心跳尚未过期，仍需在过期后重新执行既有清扫。

本轮相关后端回归 **82 passed**（含新增租户定向通知测试），前端 **5 suites / 96 passed**（新增长故障重试、快照失败重试与取消测试）；frontend lint/typecheck/i18n、增量架构守卫通过。`utils.py` Ruff 仅剩原有正则表达式中的 6 条 RUF001，新增代码无诊断。截图：本地 `.venv/f074-evidence/worker-crash.jpg`。

### 子 Agent 模型禁用

创建独立测试子模型（id=2，主模型仍为 id=1），任务 `c12d02ad3e904e929c28ec7dd767c472` 先进入人工等待点。通过正式模型管理 API 将子模型下线，再回答继续：恢复校验拒绝执行，任务进入 failed 且发出 error_message，事件中没有计算成员调用。测试通过 finally 恢复子模型上线（业务码 200）。该证据覆盖恢复边界的成员资源复核；不混同于长时间工具调用内部的即时撤权。

## 2026-10-13 工作空间页面续测

在用户明确允许验证码操作后，通过 `http://localhost:3001/admin` 正常登录测试账号，再进入 4001 工作空间；未注入 Cookie 或绕过鉴权。

- 成员抽屉：新增、名称、职责、独立模型、提示词编辑及应用可用；任务提交前正确显示两位成员摘要。
- 新环境默认勾选联网搜索和代码执行器，但服务尚未配置。首条任务正常显示失败及具体初始化错误。通过界面关闭测试账号的两个默认工具选项后继续测试，未修改系统工具权限或沙箱安全要求。
- 从页面实际提交双成员任务，会话 `45fc72759a0f4f8c9dae7272f31a8e82`：页面计算员和页面核验员均完成，主智能体汇总 23×29=667。
- 人工确认与多页面：会话 `c70e1439f0e046968ddc2ee51db7d6ed`，在一个页面选择 A 并确认，两个页面均收到子 Agent 完成结果 31×37=1147；刷新后配置、答案与状态保留。
- 历史配置：任务内新增只读成员入口，名称/职责/模型/工具/提示词锁定查看，保留新一轮输入区独立配置能力。

实际发现并修复三处问题：

1. 已完成任务刷新仅加载数据库压缩历史，部分过程文本和耗时丢失。现在非空 Harness 配置的终态任务也加载一次完整事件快照；终态不创建 WebSocket，旧任务和分享页不请求私有补播。
2. 已完成/失败任务缺少查看冻结成员配置的入口。在 TaskTurnPanel 内提供只读配置抽屉，分享页不暴露此入口。
3. 其他页面或历史补播的人工回答摘要为空。`user_input_completed` 现在读取持久化 `call_user_input.user_input`，保留旧 role=user 协议回退；重新打开第二页面已确认显示 A。

该阶段新增 3 条 hook 回归，前端合计 **5 suites / 94 passed**（后续故障验证增加至 96）；lint、typecheck、i18n 与增量架构守卫通过。截图在本地忽略目录 `.venv/f074-evidence/`：`members-readonly.jpg`、`completed-members.jpg`。

浏览器 CDP offline 模拟没有切断已经建立的 WebSocket，因此不能证明真实浏览器断线自动重连；模拟已恢复在线。真实 WebSocket 客户端主动关闭后逐事件补读的通过证据仍见下表，二者不混报。

## 2026-10-13 真实联调结果

使用当前分支源码启动 API 与独立 Linsight Worker，Docker 项目为 `jinbao-f074`，独立命名卷，端口仅绑定 127.0.0.1。运行 MySQL 8、Redis 7.0.4、OpenFGA、ES 8.12、MinIO、etcd、Milvus 2.5.10；OpenFGA migrate 成功退出。

| 场景 | 实际结果与证据 |
|---|---|
| MySQL 迁移 | `alembic upgrade head` 成功；当前 revision 为 `f074_agent_config`，`agent_config` 类型为 JSON；重复升级退出码 0 |
| 真实模型双成员 | 使用用户指定的 `cline-pass/deepseek-v4.1-flash`。任务 `f99565d4d7d04c1ba288b9d0274acb37` 完成；计算成员与核验成员各有独立 invocation_id，主 Agent 汇总 17×23=391 |
| 配置冻结、事件落库 | API 返回冻结成员/模型配置；任务保存 1,074 条完整事件，最终状态 completed |
| 真实 WebSocket 断线补播 | 首条初始化事件之后读取 15 条便关闭连接，以最后游标重连补回 1,058 条；逐条 ID 与原快照比对，顺序一致、无重无漏 |
| 游标失效 | `after=1-0` 返回 `replay_reset`；真实 Redis 裁剪后旧游标失效，快照标记历史不完整 |
| 人工等待后换进程恢复 | 任务 `1490cd8444a74ce5bbc920c111dd85df` 调用 ask_user 等待 A/B；强制结束 API 与整个 Worker 进程树后重启；93 条既有事件和待回答状态保留。提交 A 后新 Worker 接续原 checkpoint，调用计算成员并输出 A、399；最终 243 条事件 |
| 真实鉴权 | 经正常注册/登录取得 JWT；匿名快照请求 HTTP 401，另一用户读取任务返回业务 403；不存在的工具引用通过 SSE error 返回 11071 |
| Redis Lua | 真实 Redis 验证并发追加、生产者幂等、原子快照、游标补读、终态、裁剪与去重索引同步清理 |

新增可复现专项测试 `src/backend/test/linsight/test_harness_live.py`，实跑 **5 passed**，未配置环境变量时跳过。使用已有完成任务进行只读校验，Redis 探针使用独立 UUID 并在结束后只清理自身键。

```powershell
# cwd: src/backend; these ignored files were created for this local integration.
$env:config = (Resolve-Path .venv/f074-runtime.yaml).Path.Replace('\', '/')
$env:PYTHONUTF8 = '1'
$env:F074_LIVE_AUTH_FILE = (Resolve-Path .venv/f074-login.json).Path
$env:F074_LIVE_TASK_ID = Get-Content .venv/f074-task-id.txt
$env:F074_LIVE_REDIS_URL = 'redis://127.0.0.1:6379/1'
uv run pytest test/linsight/test_harness_live.py -q
docker compose -p jinbao-f074 -f .venv/f074-compose.yaml ps
```

本地配置、凭据、PID、原始事件及运行日志均放在已忽略的 `.venv/f074-*` 中，不提交。API 健康检查 `http://127.0.0.1:7860/health`；API/Worker 日志为 `f074-api-restart.*.log` / `f074-worker-restart.*.log`。中间件与进程保持运行供后续联调。

### 当前边界

- 自动注入测试登录态曾被策略拒绝，后续已改走用户允许的正常验证码登录。浏览器 API 断连恢复、失败/终止、暂停后禁用成员模型已验证；这些资源、权限与分享场景随后已补齐，见 2026-10-14 验证表。
- 未提供 E2B 配置，因此没有真实云端代码执行、文件回传或沙箱隔离成功结论。本次算术任务未选择代码工具。
- 换进程恢复发生在已持久化的人工等待点；这不证明任意执行点崩溃后可自动接管或安全重放有副作用的工具。
- MySQL 已验证，DM8 未验证；长期记忆仅验证接口，实际提供者仍由其他负责人交付。
- 模型端一次带 `max_tokens=20` 的探针返回上游 500，随后正常调用及平台流式调用均成功，未为该网关增加特殊兼容代码。

## 2026-10-14 资源、权限与页面补充验证

测试只使用本次创建的本地数据。独立运行配置开启多租户，tenant 1 为根租户、tenant 2 为挂载到测试部门的子租户；通过正常登录取得各用户 JWT。所有凭据及原始响应留在被忽略的 `.venv/f074-*`，不纳入提交。

| 场景 | 证据与结论 |
|---|---|
| Skill + 注册连接器 | 任务 `4df934393db24873986599c790d2ed6d` 实际读取所选 Skill，再调用已注册 OpenAPI 动作 `f074_multiply(67,71)`。4757 与仅存在于 Skill/HTTP 服务中的独立校验标记完全一致，服务端记录到真实请求；主 Agent 汇总并生成 MinIO 产物。未使用代码解释器，不代表 E2B 通过 |
| 普通用户授权工具 | 根管理员授予测试观察者工具查看权限；初次运行发现 Worker 未初始化 OpenFGA。补齐独立进程生命周期后重新运行，成功到达人工等待点 |
| 等待后工具撤权 | 任务 `29a65c81953940e8a8d9eebabd250779` 在等待点撤销工具权限；回答后恢复校验明确失败，HTTP 服务没有收到新调用 |
| 等待后 Skill 禁用 | 任务 `6b9485c688df4a96b6cf2406a3fa6fba` 在等待点禁用 Skill；恢复时明确失败，HTTP 服务没有收到新调用。测试后恢复 Skill 启用并撤销临时工具授权 |
| 跨用户/租户访问 | 同租户观察者和 leaf_tenant_id=2 用户不能读取根用户私有任务；带有效 share-token 可读取授权版本历史，配置仅剩成员 id/name/description；即使带 share-token，私有 replay-snapshot 仍返回业务 403。观察者尝试执行也被拒绝 |
| 分享页 | 修复父组件漏传 readOnly 后的路由兜底；实际页面不再显示成员配置与任务操作入口，不订阅私有流；新增分享/所有者两条组件测试 |
| 历史裁剪降级 | 备份本套件失败任务 `8ee624fb38d240ec8cba400441069170` 的测试流后精确裁剪至 1 条。快照 complete_history=false，刷新页面实际显示“过程记录不完整，已恢复可用历史。”并显示失败终态；截图保存在 `.venv/f074-evidence/history-trim.png` |

首个连接器测试因测试 OpenAPI 参数缺少平台要求的 description 而失败；修正测试资源后重新验证成功，没有修改通用 OpenAPI 解析行为。独立 Worker 的权限初始化与成员 tenant_id 修复有回归保护。跨租户浏览器逐项点击和复杂文件澄清仍未完整执行，API 证据不冒充浏览器证据。

本轮最终专项：后端 16 个文件 **132 passed**（与前轮 137 项选择范围不同，不相加）；前端 6 suites **98 passed**；frontend 根目录 lint/typecheck/check-i18n 通过。19 个新增 Python 文件 Ruff 通过，修改文件新增诊断为零，408 条既有诊断单列。旧分享单测补齐既有反馈查询的 mock，保持离线测试不访问数据库。

## 原离线覆盖与剩余验收

| 验收范围 | 当前证据 | 未完成项 |
|---|---|---|
| AC-01、AC-03、AC-05 | 页面配置、真实任务、只读快照、刷新回显及实际 Skill/连接器调用 | 更多连接器类型 |
| AC-02、AC-07、AC-08、AC-09、AC-10 | 真实模型双成员调度与汇总；原生图身份测试；真实失败/终止页面状态 | 无新增本地阻塞 |
| AC-04、AC-19 | 真实 JWT/OpenFGA、工具撤权/Skill 禁用、跨租户 API 拒绝、分享脱敏及浏览器只读 | 跨租户浏览器逐项交互 |
| AC-06、AC-17 | 旧事件映射、旧研究 Agent、技能与多轮相关回归 | 历史任务生产形态回放 |
| AC-11、AC-12、AC-13 | 真实 Redis/WebSocket 补播一致性；页面刷新、多页面同步、API 断连重连及浏览器历史裁剪提示通过 | Redis 持久化故障窗口不承诺零丢失 |
| AC-14 | 人工等待点换 API/Worker 恢复通过；页面澄清提交、另一页面/刷新答案回显通过 | 更复杂的文件参与澄清 |
| AC-15 | 真实 Worker 强制结束，新 Worker 清扫后页面收到失败；不自动重放 | 任意崩溃自动续跑仍未支持，启动清扫边界见上文 |
| AC-16 | E2B 文件协议离线测试、拒绝本机回退测试 | E2B 真实调用与产物下载 |
| AC-18 | MemoryAdapter 可信身份/来源与 unavailable 测试 | 其他负责人提供的实际记忆服务 |

## 初次环境探测（已由上述联调更新）

- 2026-10-13：本地 API 7860、Redis 6379、MySQL 3306 不可连接。
- Client 4001 可连接，但浏览器配置请求返回 500，停留在系统错误页。
- 初次探测没有运行真实任务；后续在用户授权后创建独立 Docker 环境和测试账号，真实结果见上文。

可复现清单见 [e2e-checklist.md](./e2e-checklist.md)。

## 自动恢复增量验收（2026-10-14）

已完成真实主/子模型强杀、双 Worker 及重复队列、暂停旧进程并在接管后复活、未知 HTTP 调用阻断、用户终止和 MinIO 文件恢复；页面确认自动接续提示与成员完成状态。详情、任务 ID、测试层级及限制统一记录于 [automatic-recovery-validation.md](./automatic-recovery-validation.md)。长期记忆提供者、全平台 DM8、灾备切换和长时间压力仍未纳入本次通过范围。

## 界面测试四修与子代理槽位验证（2026-10-16）

界面全流程测试（会话 `d6e341bd…`，任务 `7336135b…` + 追问 `3a79b71f…`）发现四个问题，同日修复并验证：

1. **零工具成员顶替内置研究员**：成员 #0 曾占用 `general-purpose` 槽位，导致「零工具成员 + 委派资料研究员」场景下研究员不可用（子代理 6 次无效 ls/glob/grep 后如实报告工具缺失）。修复：每个成员独占 `member-<id>` 槽位，研究员恒在。
2. **委派失败无止损**：提示词新增止损策略——子代理回报“缺少工具/无法执行”时，自己做或改用内置角色重试一次，不反复委派等价描述。
3. **引用无据**：提示词要求只引用实际读到的内容；仅有搜索 snippet 时不写精确文件名/日期/条款号。
4. **成员库 UI**：零工具成员在协作成员下拉带「无工具」徽标（仅提示，不拦截保存）；模型选择器补 accessible name。

真实验证（任务 `8fc404b2…`：零工具成员 + 委派 general-purpose + 勾选 web_search）：`Available subagent types` 同时列出 general-purpose / member-analyst-check / data-analyst / chart-designer / quality-reviewer；被委派的研究员实际执行多轮 web_search（宁波银行、江苏银行、南京银行年报等）并回传带出处摘要，主代理汇总 COMPLETED。界面侧复核：下拉中 2 个零工具成员显示「无工具」徽标，有工具成员不显示（截图 `.venv/f074-logs/f074-subagent-picker-ui.png`）。

测试：后端 `test/linsight` 917 passed / 15 failed——15 项全部为遗留失败（workspace 路径命名空间 6 项为 Windows 平台假设、workspace_backend 3 项为 9-28 MinIO 接口重构后测试替身未同步、step_persistence 3 项为 fenced 写改造后 mock 未同步、code_interpreter 相关 3 项 10-14 即有失败留档），与本轮修复无交集；前端 Linsight 区域 213 passed；ruff 无新增非 RUF00x 项；eslint / typecheck / check-i18n 通过。

## 遗留测试失败清理与 docx 耗时排查（2026-10-16 追加）

把上一节列出的 15 个遗留失败全部查明并修复；其中 3 个顺带修出**真实的生产代码跨平台 bug**（此前只在 Linux 上跑所以未暴露）：

1. `workspace_backend.strip_executor_host_prefix`：`os.path.normpath` 在 Windows 上把 POSIX 宿主前缀 `/root/.cache/...` 改写成 `\root\.cache\...`，折叠静默失效；且 Windows 风格宿主路径（`C:\...\output\a.png`）被「必须以 / 开头」判定直接拒绝。改为显式双方言比较（POSIX 与盘符路径都按正斜杠前缀匹配），Linux 行为逐字节不变。
2. `local_executor._kill_process_tree`（Windows 分支）：原来只 `proc.kill()` 直接子进程，幸存的孙进程（LibreOffice/pandoc/sleep）握着 stdout/stderr 管道，使后续 `communicate()` 一直阻塞——「超时」在 Windows 上从未真正中止调用。改为 `taskkill /F /T` 杀整树（Linux 的 killpg 路径不变）。
3. `local_executor._relocate_root_files`：搬迁通知经 `os.path.relpath` 在 Windows 上产出 `output\report.xlsx`，而该通知是给模型读的沙箱路径（POSIX 方言）。改为 `output/<name>` 常量拼接。

其余为测试侧对齐：`FakeMinioStorage` 补 `list_objects_sync`（9-28 MinIO 接口重构后遗留）；`test_step_persistence` 的 Redis 替身改捕获 `_fenced_cache_write`（fenced 写改造后遗留）；`test_e2b_copy_in_out` 用 `os.utime` 消除 mtime 栅格 flaky；`test_code_interpreter_timeout` 的存活探测在 Windows 改用 `OpenProcess + GetExitCodeProcess`（`os.kill(pid, 0)` 在 Windows 会走 TerminateProcess 而非探测）。

**验证**：后端 `test/linsight` **932 passed / 0 failed**（此前 917+15）；client 全量 `pnpm lint` rc=0（清掉 explore.test.tsx 的 any×2 + 中文数据、jsdomEnvironment.js 的 CJS require 豁免共 5 项）。Worker 重启部署后真机复验通过（任务 `fae26858…` 10s 完成）。

**docx 任务耗时排查**（对应 58f14e6b / b935a4c / 6015c273 的 10 分钟级运行）：耗时大头不是环境安装（npm+apt 合计约 1 分钟），而是沙箱内的「改写脚本 → 转 PDF/拆页 → 读图核对」循环（旧任务约 20 次 code interpreter 调用，每次 15-25s）。在当前部署下复现（任务 `6a0583a3…`，docx 技能 + web_search + code interpreter）：委派优先与 `run_in_background` 生效（子代理并行检索）、「转换/美化最多 2 轮」收敛生效（docx 生成→核对一轮→完整性与图片检查→收尾，无第二轮改写），实际执行约 6.5 分钟（另含 3.5 分钟等待用户澄清）。遗留观察项：① 澄清层面问了「受众/数据口径/附加格式」三个默认假设本可覆盖的问题；② 主代理自行 web_search 约 27 次，与子代理检索存在部分重复；③ E2B 仍是默认裸模板，每个 docx 任务都要现装 libreoffice/poppler/依赖包（自定义模板可省约 1.5-2.5 分钟/任务，需构建云端模板并在工具组配置 template）。

## E2B 预装模板 `bisheng-linsight-v1`（2026-10-16 落地）

针对上一节的遗留观察项 ③：已在 E2B 官方云构建并接入自定义模板 `bisheng-linsight-v1`（2 vCPU / 2048 MiB）。

- **资产**：`docker/e2b-template/e2b.Dockerfile`（继承 Docker Hub `e2bdev/code-interpreter:latest`）。两个构建期坑已写进 Dockerfile 注释：① 不能用 E2B 模板名做 `FROM`（须用 Docker Hub 镜像引用）；② `e2b template create` 必须显式给 `-c` 启动命令与 `--ready-cmd`——官方镜像变体用 `/root/.jupyter/start-up.sh` 启动 Jupyter（49999），缺启动命令时构建能过但运行时每次报 `sandbox is running but port is not open`。
- **预装内容**（相对官方基线的缺口，由 `f074-e2b-template-probe.py` 实测确定）：libreoffice-writer/impress/calc、poppler-utils、pandoc、qpdf；python-pptx / reportlab / pdfplumber / pypdf / xlsxwriter；npm 全局 docx / pptxgenjs / exceljs 并软链到 `/node_modules`（E2B 不保留 Dockerfile ENV，NODE_PATH 方案实测无效）。
- **接入**：`E2bCodeExecutor(template=...)` 透传 `Sandbox(template=...)`（缺省 None → 官方默认模板，行为不变）；工具组「代码执行器」extra 增加 `config.e2b.template`（旧值备份于 `.venv/f074-e2b-tools-extra-backup.json`）。
- **验证**：模板探测 `run_code('KERNEL_OK')` + soffice docx→pdf 转换 + 11 个 Python 库 + node require 全通过；真机 docx 任务 `c36bafe4…` 端到端 COMPLETED（322s：检索 ~150s + 文档生成 ~170s），**全程无 apt/npm/pip 安装**（此前每次任务 40-70s 安装 + 安装失败风险），交付 3 页 docx + md + 趋势图。

## 检索循环修复（2026-10-16，会话 a9fa51aa）

用户复跑 docx 简报时主代理陷入自搜循环：任务 `bdffc2b2…` 在 2.5 分钟内发出 40+ 次 web_search（结果塌缩到同几篇文章：21 经济网“连续11季度下滑”、金融负面舆情周报反复出现），**0 次委派**，被用户手动终止。

**根因**（三层检测都看不见这个形状）：每次 query 不同 → 同 (args+result) 身份类检测无法命中；同一工具名重复（周期 1）→ 周期 2-6 的 n-gram 循环检测挂不上（需要 ≥2 种工具名）；没有“同工具换参高频”的计数规则。

**修复**：

1. 熔断器新增**检索预算**（tool_loop_middleware）：`web_search` / `search_knowledge_base` 在最近 48 条工具结果内累计调用（忽略参数）≥ 软阈值时向模型注入收敛提示（主图 12 / 子图 20），≥ 硬阈值且仍在调用即硬停并 salvage（主图 24 / 子图 36；子代理是检索主力，阈值放宽）。其余工具（read_file / 代码执行）不受限——它们按不同参数合法重复。
2. 提示词新增【检索有预算，重复即停】（主模板，无工具名硬编码，遵守 lockstep）：累计超约 10 次要警觉，重复即换委派或收尾。
3. 测试：middleware 新增 7 用例（软阈值注入、阈值下不注入、非检索工具豁免、硬停、恢复豁免、窗口内交错计数）；提示词断言 2 条。

**真机复现**（任务 `46421a87…`，与用户完全相同的 prompt / skills(docx+pdf) / 工具 / 协作成员）：**第一步即委派资料研究员**并拿到带出处摘要，主代理只做补充检索；web_search 共 **14 次**（旧 40+），437s 自然 COMPLETED，交付 docx + md + PNG。本次运行软/硬闸门均未触发（搜索分散在窗口内、计数被其它步骤稀释）——收敛主要来自提示词规则与委派行为，闸门作为极端场景兜底（单测覆盖）。回归：`test/linsight` 938 passed / 0 failed。
