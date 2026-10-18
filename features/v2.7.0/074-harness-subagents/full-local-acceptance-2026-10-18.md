# 本地开发功能完整回归（2026-10-18）

## 范围与结论

范围为用户此前约定的 Harness 自主任务、E2B 沙箱、Agentic Loop、工具与连接器、独立子 Agent、Skill 对象存储、流式事件、重连、人工输入、多轮继续及故障恢复；记忆按用户约定排除。不是整个毕昇所有业务模块的验收，也不代表所有外部工具均测试过。

真实环境为本机 API :7860、平台 :3001、客户端 :4001、专用 Linsight Worker，连接已有云端数据库与中间件。按用户确认，没有需要本轮重启的云端 FastAPI 实例。测试只创建带 FULL 标记的合成内容，没有修改业务文件或远端凭据、没有推送 Git 远端。

主流程通过；本轮发现并修复两处代码缺陷及一处本地平台启动配置错误。但完整质量门禁未全绿，原业务 Skill 文件缺失与一次本地 API 异常退出仍保留，不能宣布所有功能/环境均已完全验收。

## 功能矩阵

| 能力 | 本轮实际验证与结果 |
|---|---|
| 普通对话 | 网页新建普通对话，实际返回 FULL-CHAT-OK，未要求配置沙箱才能对话 |
| 无成员 Harness / 自主循环 | 新任务 subagents=[]，实际代码/搜索、文件读写、人工等待及恢复；真实工具输出经模型处理后产生结果 |
| 云端沙箱 / 隔离执行 | bisheng_code_interpreter 使用现有 E2B 配置，平方和 338350，stderr 空、error null；生成文件并从云端读回。没有回退到宿主机执行；未做完整沙箱逃逸渗透测试 |
| 联网搜索 | 新任务实际调用 web_search 一次，返回官方 Python 文档等真实网页结果和链接 |
| 成员独立配置 | 网页创建 FULL-计算员-1018 与 FULL-核验员-1018，分别配置名称、描述、提示词、模型、Skill、工具和连接器，并保存为常用成员。快照持久化两个成员，资源列表不同 |
| 路由与整合 | 主 Agent 先调计算成员的 E2B 生成 CSV，再调核验成员读取原件及产物并核验，整合为 CSV 与报告；两个成员均完成，有真实完成卡片及耗时 |
| 工具与连接器 | 计算器实际返回 400；时间工具返回实际时间；getAgeByName 连接器使用合成输入 Michael 返回结构化数据。底层回执名为 tool_type_38_6f6b1fc404aa11eb3508d5fa56766d76，与配置的连接器 131 对应；没有发送消息、邮件等外部写操作 |
| CSV 上传与产物 | 网页上传 full-sales.csv（4 行），沙箱读取实际附件，产出 2026-01=200、2026-02=200、总额 400。网页表格表头和两行内容正确，实际下载文件核对通过；CSV 与核验报告均从 MinIO 读回并校验 MD5 |
| 成员 Skill | 核验成员实际读取 /skills/agents/lib-3/remote-skill-fga-1018/SKILL.md，工具回执包含 FGA-SKILL-OWNER-OK |
| 流式反馈 / 重连 | 网页实际展示工具和成员过程；WebSocket 连接断开后携游标重连，核对 88 条事件，重复 0、遗漏 0；过期游标得到 replay_reset。完成任务刷新后结果可回放 |
| 人工澄清 / 多轮继续 | ask_user 暂停等待，提交 B 后写 human-choice.txt；下一轮从上下文识别 B，写出并读回 FULL-FOLLOWUP-B，网页刷新显示已回答状态和持久文件 |
| 停止 / 不复活 | 活跃任务明确终止，事件包含 task_terminated；后续两次 Worker 故障测试后仍保持 terminated |
| Worker 崩溃安全接续 | 持久文件写入完成后强杀专用测试 Worker，原任务 epoch 1→2，完成且出现 1 条 task_recovery，原事件前缀完整保留；网页显示自动接续及最终标记 |
| 沙箱恢复策略 | 代码完成回执持久化后重启专用 Worker，原任务进入 failed/blocked，reason=Sandbox process state cannot be reconstructed safely；代码完成回执仍只有 1 条，没有重跑。网页明确显示“自动续跑已停止” |
| Skill 管理与存储 | 新建 full-skill-storage-1018-r2，真实 ZIP 导入、完整包更新、旧版本不可变、独立无旧目录缓存加载、缓存删除/损坏重建、文本/二进制导出一致、非法路径包返回 11051；对象匿名 GET=403。后台网页查看新版附件并实际导出 ZIP，逐字节核对 |
| Skill 迁移 / 回退 | 对新建 full-skill-migrate-1018 执行对象→旧目录恢复→dry-run→重新迁移→重跑；content_hash 和恢复摘要一致，旧文件保留，API 详情可读。不是生产全量回退 |
| OpenFGA | 当前模型含 linsight_skill。新测试 Skill 的 owner/read/edit/manage/delete 检查全部 true，无关用户全部 false；原补偿仍为 9 succeeded、2 个已删除资源 dead，不恢复悬空授权。未新增失败 owner 登记 |
| 资源失效 | 缺失/禁用的主 Agent Skill 在任务工具执行前失败；不存在的成员工具在提交时拒绝。禁用测试仅使用自建 Skill，finally 恢复启用。匿名任务快照访问被拒绝；跨租户边界由自动化回归覆盖，未新建跨租户真实账号 |
| 迁移与健康 | 实际 Alembic revision=f035_skill_object_storage，API 重启时 upgrade head 可重复运行；收尾三个 HTTP 服务均 200、专用队列为 0 |

## 本轮修复

1. **主 Agent Skill 失效被静默忽略**：skill_provisioning 仅取 selected 与 enabled 的交集，可能让任务在未加载指定技能时继续。改为先校验所有选中项均可用，再复制任何文件；无效选择不泄漏是否属于其他租户。更新禁用/未知选择回归，真实任务确认 failed 且无工具调用。提交接口可先创建任务，执行期装配明确失败，不声称所有主 Skill 错误都已提前到 HTTP 提交期。
2. **恢复阻断提示相互矛盾**：recoveryAttempt 存在时，无论结果是否 blocked 都显示“已自动接续”。现在 recovery_blocked 不显示该成功提示，保留阻断原因和处理入口；新增切换状态测试，真实故障任务网页复核通过。
3. **本机平台 MinIO 代理仍指旧地址**：忽略的 f074-start-platform.cmd 中环境变量覆盖了已正确配置的 .env.development.local，导致代理仍走 127.0.0.1:9100。对齐后端云端 sharepoint=172.19.70.108:9000 并重启本地平台；实际签名文件经 :3001 和 :4001 均 HTTP 200。未修改共享 MinIO 策略或凭据，也未把机器专用配置提交仓库。

## 自动化与质量检查

| 检查 | 最终结果 |
|---|---|
| Linsight + Alembic 单 head + 本轮权限模型/发布脚本相关 | 修复后 1113 passed、8 skipped、8 warnings，122.78 秒；包含此前 Skill/恢复等回归。不是 backend/test 全仓库 |
| 主 Skill 校验定向 | 12 passed，包含于上面的全量，不相加 |
| Harness 前端 | 修复后 20 suites / 232 passed |
| 前端补播游标 | 1 suite / 1 passed，与上项不同选择范围 |
| 客户端完整 Jest | 按 NODE_ENV=development 最终 66 suites passed、5 failed；584 tests passed、6 failed，590 total。与本轮修复前首次完整运行的失败套件集合一致；新增回归通过，不把完整套件写成全绿 |
| 全工作区类型检查 | 通过；前端修复后再次执行客户端 typecheck 通过 |
| 国际化 | check-i18n 通过，无新增 key drift；本次 UI 修复未新增文案 |
| ESLint | 客户端 lint 通过；根 lint 在平台现有 54 项 no-explicit-any 处失败，后续包未由这次根命令执行，不能宣称全工作区 lint 通过 |
| 增量 Ruff / arch-guard / diff | 修改的后端文件 Ruff 通过；提交前检查相关改动，未放宽规则或新增 suppressions |

客户端完整测试未通过项：

- pages/Subscription/ChannelSettings/ChannelSettingsPage.test.tsx：渲染组件类型 undefined；需继续区分 mock/依赖问题和实际页面问题。
- hooks/useNotificationsFromUrl.test.ts：URL 参数解析期望 42，实际 null。
- pages/knowledge/hooks/useFileManager.test.ts：LoadMore/infinite-scroll 结构性断言失败。
- pages/knowledge/hooks/useFileUpload.test.ts、hooks/mapSessionVersionStatus.test.ts：引入 sse.js ESM 时 Jest 转换失败，不能作为这些套件的功能通过证据。

## 未解决与边界

- **API 稳定性**：首次 Worker 故障注入使用 taskkill /T 后，观察到本机 API 无监听，探针出现连接重置/拒绝；API 日志没有可确认的正常退出原因，未取得可归因的系统崩溃事件，不能断言是产品代码、资源压力或故障注入的进程树影响。恢复 API 后原任务已经从 checkpoint 完成。第二次通过 Worker 重启脚本注入沙箱故障时 API 持续可用。此项须单独跟踪，不能用重启后的健康掩盖。
- **5 个旧业务 Skill 仍缺源文件**。本轮只读迁移预览结果 5 missing、7 already_done，预期返回非零。没有创建占位正文或修改原业务元数据。
- **质量门禁尚未全绿**：保留上述 5 个客户端失败套件及后台 54 项 lint 问题，不将其统称为“仅环境问题”。权限模块之前已确认的 27 项基线失败不在本轮选定的 1113 项范围内，也未因本轮修复而解决。
- 当前只有一个可用对话模型，验证了成员模型配置和快照，未验证不同供应商/模型组合。未测试长期记忆、DM8 实机、多物理机、真实跨账号分享端到端、所有外部工具的副作用恢复、生产 GC、生产级并发/长时压力测试。
- 安全阻断是预期策略结果，不等于沙箱任务支持任意崩溃接续。已同步文件不代表 Python 变量、模块、后台进程或连接状态可恢复。

## 任务与证据

| 场景 | task / chat |
|---|---|
| 双成员网页 | 25c2faa9bbfb42d1aa86a417376cb2cb / 7762fd1636944ea3b2d6a02f9cd9d142 |
| 无成员沙箱 | 347a89354f5f4a598c2c84e4771d5227 / f37e909e027641abbe36aa492dd1f2b6 |
| 联网搜索 | fbfac5d3471744749636ece506c7a7c4 / b17ef090459d465bba44f6d9137f5493 |
| 人工输入及追问 | ea357b22f0e640a5957dede65a4f9fbe / 2b1f951a68ef478d83f28f256a52a11e |
| 主动停止 | 7fcb333fbd944190b8c94806cd349569 / 3f74c97924314b7d8d8d20bdf4988b84 |
| 文件任务崩溃接续 | d942add3383544549592de1046c755b9 / ffa622a2eedf411e94aada0fe9566c8a |
| 沙箱崩溃阻断 | 567e83a7a0b741908c1310ce8fcc55f3 / fa5b9068a87d4dec9f120d95e420ab58 |
| 缺失/禁用 Skill | 8d4db61dc64a4466ab1a36972f7bbcaf / d14a4ec9e10a4718bed9a3304dc0e899（两项 task id） |

忽略目录 src/backend/.venv/f074-evidence/ 保存 review-1018-full-*、full-permission-final-proof.json、full-skill-storage-proof.json、full-skill-migration-proof.json、full-migration-preview.jsonl、full-final-health-cleanup.json 与 full-browser-*.jpg。日志为 .venv/f074-logs/full-acceptance-*。浏览器下载的 full-monthly.csv 和 full-skill-storage-1018-r2.skill.zip 已实际核对。收尾终止了本轮失败探针创建的 3 个未启动合成草稿，未删除记录；测试 Skill 已恢复启用，服务健康、队列为空。测试探针、配置、凭证和完整日志不提交。

本轮自审检查：缺失资源先校验后复制、失效不漏租户信息、故障只针对专用 Worker、沙箱不重放、临时禁用恢复、下载签名 Host 对齐。未调用独立审查 Agent，不声称独立评审通过。
