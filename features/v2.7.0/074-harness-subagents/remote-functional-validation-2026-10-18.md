# 云端中间件功能验证（2026-10-18）

## 配置补齐后的复测更新

用户补齐沙箱与搜索配置后，同日重新创建两项真实任务，均已完成。下文“三项尚未可用”为首次测试时的历史状态；其中 E2B 和搜索两项现已通过本次复测，Skills 文件缺失尚未解决或重新验收。

- **沙箱**：任务 `afcc9488bf0a48bb95fbb78812c20f13`，chat `ebe8be8149674aec843e138f177898b7`。数据库工具类型已为 e2b。真实 bisheng_code_interpreter 返回 Python 3.13.16 / Linux 环境、平方和 338350、stderr=[]、error=null，并生成 `output/remote-sandbox-check.txt`。最终文件从云端 MinIO 读回，内容含 `REMOTE-SANDBOX-OK 338350`，25 字节且 MD5 一致。浏览器显示代码执行回执、完成状态与交付文件。
- **搜索**：任务 `ef5fd51393164f3f8865bfcb561bf315`，chat `17c11015f5d84367be69b38e6a3b3896`。真实调用 web_search 一次，返回 Python 官方 pathlib 文档和 Real Python 等网页结果，主 Agent 引用结果链接并完成；浏览器显示“联网搜索 1 次”和实际输出，没有此前的未配置错误。
- 本轮只验证这两项配置后的执行链路，没有重复注入沙箱崩溃或外部工具副作用恢复，也未据此扩大其他验收范围。没有修改用户刚保存的工具配置或凭据。
- 新证据仍在 `src/backend/.venv/f074-evidence/`：`review-1018-remote-{sandbox,search}-reconfigured-{task,final}.json`、`remote-config-retest-proof.json`、`remote-config-retest-{sandbox,search}.jpg`。探针和日志保持忽略；仅更新本文档。

## 结论与环境

主流程通过真实模型、API、浏览器与云端存储验证，但当前环境不能视为全部功能可用：E2B 代码沙箱未配置、远端 Skill 元数据对应的本机文件缺失、联网搜索服务未配置。没有把配置失败算作成功，也没有回退到宿主机执行代码。

沿用 [启动迁移验证](remote-startup-migration-validation-2026-10-18.md) 的远端环境：MySQL/Redis/ES/Milvus/OpenFGA 为 172.19.70.107，MinIO 为 172.19.70.108。本机 API 7860、平台 3001、工作区 4001；API 与专用 Worker 均使用忽略文件 `.venv/f074-remote-runtime.yaml`。队列为 `f074_remote_verify:queue`，数据库仍与远端共享，不是独立测试数据库。未启动 Celery/Beat。

本次仅使用带 REMOTE 标记的合成问题和自建测试任务，没有修改既有业务任务。只强制终止并重启本机专用测试 Worker，没有停止另外两个远端 Worker。业务源码、远端工具凭据和模型配置均未修改。

## 实测结果

| 项目 | 结果与证据 |
|---|---|
| 普通对话 | 浏览器提交 17×23，返回 `REMOTE-CHAT-OK391`。同时页面显示联网搜索未配置告警，见下文 |
| 无成员 Harness | `subagents=[]` 正常执行 write_file/read_file，完成并交付 remote-main.txt；无需配置 E2B 即可走此链路 |
| 成员配置与路由 | 保存测试成员名称、提示词、模型 1、工具 1/2、连接器 131；独立成员实际执行，主 Agent 整合结果并交付 remote-member.txt |
| 工具和连接器 | calculator 返回 391；时间工具返回实际时间；只读公开 getAgeByName 连接器以合成输入 Michael 返回结构化数据。未测试发送消息/邮件等外部写操作 |
| 云端产物 | 两个交付文件分别 22/663 字节；实际从 MinIO 读取内容、校验标记与 MD5。浏览器刷新后仍可打开成员任务文件预览 |
| 断线重连 | 真实 WebSocket 接收后断开、携带游标重连；308 条后续事件按序一致，缺失 0、重复 0；失效游标返回 replay_reset |
| Worker 崩溃恢复 | 无成员任务完成 write_file 持久化回执后，强制终止专用 Worker 进程树并重启。任务 epoch 1→2，出现 1 条 task_recovery，历史事件保留，恢复后完成并返回目标标记 |
| 停止任务 | 实际执行中调用终止接口，状态为 terminated，存在 task_terminated；浏览器重新打开显示“任务已终止” |
| 最终运行状态 | API、平台、工作区均 HTTP 200；Redis PING 成功，专用队列长度 0，共 3 个 Worker 心跳；本次六个已创建任务均处于终态 |

首次较长恢复探针在 120 秒内没有到达首个持久化写入触发点，因此没有注入故障；该任务后来正常 completed、epoch=1。随后缩短目标重测并实际完成崩溃恢复。首次探针的观察超时不是恢复机制失败，也不算恢复通过的证据。

## 尚未可用的三项

1. **E2B 代码沙箱**：云端代码工具配置为 `executor_type=local`，未配置 E2B API Key 与模板。真实代码任务失败，业务错误 11090，详情为 `Harness code execution requires an E2B sandbox`。未在宿主机执行该测试代码。需要将代码工具正确配置为 E2B 并补齐有效凭据/模板后再验收真实云端执行和沙箱恢复。
2. **Skills 文件**：云端存在 5 条启用的 Skill 元数据。抽查 financial-statement-analysis、loan-repayment-plan，两者详情的正文、预览、文件列表均为空；选择前者提交成员任务实际被 11071 `Skill bundle is missing` 拦截，未生成任务。当前 SkillStore 根目录为 `src/backend/data/linsight_skills`，对应正文文件缺失。切换数据库不会搬迁磁盘文件；需同步实际 Skill bundle 或挂载正确共享卷，再验证加载与执行。没有用占位文件替代业务 Skill。
3. **联网搜索**：普通对话虽返回正确答案，但出现“联网搜索失败 / 联网搜索未配置”告警，提示在平台“内置工具→联网搜索”配置服务商及 API Key。需要补齐搜索配置并复测。该问题不能通过回答成功掩盖。

本次没有借用本地其他环境的密钥来修改共享工具配置。ES 仍有上次已记录的单节点副本未分配问题，未调整共享集群参数。

## 测试数据与范围

| 标签 | 任务 ID | 最终状态 / epoch |
|---|---|---|
| remote-memberless | 6b3cf273317a4f90afdef8089985e282 | completed / 1 |
| remote-member | 68fbc7779532417184cf5ca51e197fe4 | completed / 1 |
| remote-code-config | 068588cae6fd4ab79efa0adfc5c4a5e5 | failed / 1，预期配置阻断 |
| remote-recovery | 5d005dcde1eb4fba9573c312143b8b1e | completed / 1，未注入故障 |
| remote-recovery-short | 279a4eff1b1c469e872161e9a3d57182 | completed / 2，实际故障恢复 |
| remote-stop | b937749a2aba4f26a16262720f070b89 | terminated / 1 |

普通对话 chat ID 为 `6653a211ad144e94a461f061c61e18f8`。自建成员模板 ID=1，名称 `REMOTE-VERIFY-1018-tools`，非默认，保留供核对。未永久清除测试记录。

远端工作区只有一个可用对话模型 deepseek-v4-flash（ID=1），本次没有验证不同模型的成员组合。没有验证长期记忆、RAG 检索/知识导入、Celery 作业、真实 E2B 执行及外部工具副作用后的故障恢复；这些不能由本次主流程测试推导为已通过。

## 本地证据

证据位于 `src/backend/.venv/f074-evidence/`，包含运行数据，留在忽略目录，不提交配置、登录凭据或完整日志：

- `remote-functional-proof.json`：真实 MinIO 内容/MD5 与工具结果。
- `review-1018-remote-reconnect-proof.json`：WebSocket 重连事件比对。
- `review-1018-remote-recovery-short-{before,final,proof}.json`：故障前、恢复后与断点续跑证明。
- `review-1018-remote-stop-final.json`、`remote-functional-final-check.json`：停止状态、全任务终态与健康检查。
- `remote-functional-code-config.json`、`review-1018-remote-code-config-final.json`：沙箱配置阻断。
- `remote-functional-skill-assets.json`、`review-1018-remote-skills-blocked.json`：Skill 文件缺失与真实提交阻断。
- `remote-functional-{chat,member-browser,code-blocked,stop-browser}.jpg`：浏览器结果截图。

探针位于 `.venv/f074-tools/f074-remote-functional-*.py`，日志位于 `.venv/f074-logs/remote-functional-*.log`。临时浏览器测试页已关闭，原用户页面保留。环境继续连接云端中间件；本次仅验证文档进入本地 Git 提交，不推送远端。
