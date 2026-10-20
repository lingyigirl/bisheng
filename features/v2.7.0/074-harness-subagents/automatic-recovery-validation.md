# Harness 执行中崩溃恢复：实现与验收

日期：2026-10-14。用户已确认 crash-recovery-proposal.md，所有改动仅在本地分支 feat/2.7.0/beijing-ns-llm-guardrail；没有 push 或创建合并请求。

## 交付行为

新 Harness 任务保存 recovery_protocol=1。Worker 每 5 秒扫描失去执行租约的任务；租约为 30 秒、每 10 秒续期。恢复保持原任务 ID、LangGraph thread_id 和成员快照，通过 astream(None) 接续 checkpoint，不提交新问题或伪造人工答案。默认最多执行 3 次自动接续，按 10/20/40 秒退避；租约有效期间不会抢占。

Redis 随机 token 与数据库 recovery_epoch 共同约束执行。Redis 事件、checkpoint/pending writes、缓存和工具回执在写入时验证租约；SQLAlchemy 写事务锁定所属任务并检查执行代次与终止状态。正常续租导致 WATCH 失效时仅重试未提交事务，token 改变则拒绝写入。数据库领取等待期间也续租。

主、子 Agent 的模型/工具共用恢复 middleware。调用前写 started，完成后保存类型化结果及参数摘要；完成结果可复用，未知结果的外部 API/MCP、写文件及代码不自动重放。仅代码维护的内置工作区只读工具可以重试，外部同名工具不能冒充。回执数量与元数据标记交叉校验，缺失不按“从未执行”处理。

每一代执行使用独立本地目录及 MinIO attempt 前缀；复制完成后才记录 workspace_epoch。复制途中再崩溃时仍从上次完整工作区复制。跨轮继承读取上一轮实际工作区。恢复会保留已完成工具证据和已回答澄清，清除未完成的展示片段；Redis 原始事件仍保留。子调用身份在恢复后的模型/工具节点重新发布，避免卡片误显示“已停止”。

页面显示自动接续次数；恢复被阻断时显示专用三语提示及诊断详情。若完成状态已提交而最后一帧尚未发布就崩溃，订阅端重新读取数据库快照，避免永久等待最终帧。

## 真实故障注入

环境为本机独立 MySQL、Redis、MinIO，真实 DeepSeek 模型及本地 HTTP 连接器。强杀通过 Windows taskkill /T /F 结束测试 Worker 树；新 Worker 在旧租约尚余 21–29 秒时启动，验证运行期扫描，而非仅启动时清理。

| 场景 | 原任务 ID | 验证结果 |
|---|---|---|
| 主模型输出中强杀 | 037d88efb0524c6ea84f081b1cd1c37e | 原 ID completed，epoch 1→2，接续 1 次，最终 final_result |
| 子模型输出中强杀，含修复后页面 | 970d1a5057024e20bd780f4b606873a4 | 原 ID completed，epoch 2；成员 start/end 使用同一 invocation_id；页面显示“已自动接续（第 1 次）”“已完成” |
| 双 Worker + 8 条重复恢复消息 | 5361927140b94715886a805bea236de3 | 两个独立 Worker 树竞争，只有一次接管，最终 epoch 2/completed；额外 Worker 已清理 |
| 外部服务已收到请求，结果回执前强杀 | 3c08bcb902014e85a04b7247e386692b | 本地 HTTP 服务记录调用 1 次；接管后 failed/blocked，未第二次调用；返回 unknown external outcome 诊断 |
| 强杀后用户终止，再启动 Worker | b8a90799e4b14753b655f20fea6ffcab | 重启并等待超过租约后仍 terminated，epoch 1，接续 0 次 |
| 文件落盘后强杀 | 11cb57964e53457fbddb7f0225213c48 | completed；write_file 仅一条 epoch 1/done 回执；恢复产物经下载 API 从真实 MinIO 读回内容 391 |
| 旧执行代次尝试 SQL 写入 | 037d88efb0524c6ea84f081b1cd1c37e | 有效临时 Redis token 下使用旧 epoch，真实 MySQL 写入被 RecoveryLeaseLost 拒绝；事务回滚、临时租约已释放 |
| 暂停旧 Worker，接管后复活旧进程 | f6f69963387b4800bb3569f01f71199c | NtSuspendProcess 暂停 5 个测试进程，新 Worker 到 epoch 2 后 NtResumeProcess 恢复旧进程；原任务 completed，接管后旧 epoch 事件数为 0，旧进程树已清理 |
| 恢复后私有任务访问 | 970d1a5057024e20bd780f4b606873a4 | 所有者可读取快照；同租户其他用户及另一租户用户均返回业务 403 |

原始响应、注入脚本、进程日志、文件字节与截图保存在忽略目录 src/backend/.venv/f074-recovery-*，不包含在版本交付中。已恢复/清理测试注入用额外进程；不对真实业务系统做故障写入。

## 回归与检查

- 后端 20 个相关测试文件：175 passed，覆盖旧任务兼容、人工恢复、工具权限、租户、事件映射、E2B 文件协议及单 migration head；包含数据库领取慢于租约初始期限时仍持续续租的回归。
- 真实 API/Redis/MySQL 专项 test_recovery_live.py：2 passed；与离线 mock 测试分开计。
- 独立 DM8 实例：实际 agent_config + recovery_epoch/recovery_state 迁移、重复升级、旧记录默认值及 JSON 往返通过，1 passed；不等于整个平台 DM8 全量验证。
- Client 恢复投影、完成快照、卡片专项：12 passed；工作区 typecheck、check-i18n 通过。
- 实际浏览器已确认成功任务的接续次数、子 Agent 完成状态，以及失败任务的“自动续跑已停止”专用提示；截图位于 .venv/f074-recovery-completed.png。
- 新恢复模块 Ruff、涉及的 Client 文件 ESLint 通过。架构检查没有 VIOLATION，保留既有 Endpoint/legacy model 导入 WARNING。
- 全量 pnpm lint 未通过：Platform 3 个未改文件 54 项错误，Client 2 个未改文件 5 项错误，与新基线既有 59 项一致；未修改 suppressions 掩盖错误。

## 适用边界

- 支持的是具备持久状态且可安全判定的 Worker 崩溃接续，不能写成“任意故障、任意工具、任意数据丢失均无损自动恢复”。
- E2B 代码执行过后，原沙箱进程内存状态无法证明可重建，本期保守阻断崩溃接续；正常 E2B 执行及文件同步仍可用。未知外部副作用需要人工核对，不能自动重发。
- Redis/checkpoint/工具回执保留 7 天；元数据丢失、过期、无可用 checkpoint、MinIO 不可读时明确失败。基础设施整体数据丢失不在保证范围。
- 暂停进程与进程崩溃不同：如果旧进程恰好停在持锁数据库事务内，接管必须等事务锁释放；没有为“快速恢复”强行绕过数据库锁或终止其他连接。旧进程未退出时，也可能仍占有队列的阻塞消费连接，领取延迟不等于恢复失败。
- 暂停测试的前两轮观察窗口仅为 60 秒，未在窗口内看到接管；清理后任务仍完成，不能计为“复活旧进程”通过。扩大观察窗口的最终测试才在新代次开始后恢复旧进程并通过。扫描重投标记 TTL 为 30 秒，耗时还受排队、数据库锁及模型响应影响，不承诺固定 30 秒内恢复。
- 不升级旧任务的恢复协议，不对缺少工具回执的历史任务猜测副作用；长期记忆仍由其他负责人提供。
- 未做数小时压力、整机断电、Redis/MinIO 灾备切换或全平台 DM8 验收。

## 部署与回滚

新增单链迁移 f074_recovery，父 revision 为 f074_agent_config。独立 MySQL 联调库已升级；正常部署先迁移再同时更新 API/Worker。旧版本代码可忽略新增 nullable JSON 与带默认值的 epoch 列；回滚应用不执行删列。回滚前应停止提交新协议任务并等待/明确终止在途任务，不能让旧 Worker 继续消费恢复消息。
