# 端到端验证记录

日期：2026-10-19。整体：**PARTIAL**。已执行真实浏览器和真实渲染验证；专用双租户 HTTP 套件与信创性能矩阵尚未完成，不能合并为“全部 AC 已通过”。

同日新增“除记忆外的 Harness 模块”专项实测：8 个真实模型任务、两次实际 Worker 中断、真实 WebSocket/Redis/MySQL 验证。文件任务可以恢复；有状态沙箱恢复返回 11090 并安全停止，不能声明任意任务无损续跑。详见 [Harness 模块验收记录](./harness-module-validation-2026-10-19.md)。

同日按用户要求，将任务模式缺少模型容量资料时的默认摘要压缩阈值由 170,000 改为 **200,000 tokens**，保留最近 6 条消息；已知容量模型仍使用原有容量比例规则，普通对话 8,000 tokens 历史预算不变。配置由项目 `context_compaction.py` 管理，通过 HarnessProfile 仅排除 SDK 默认摘要组件，并为主 Agent、内置和自定义成员分别装配独立实例；没有修改依赖包或伪造模型容量。阈值边界、较小模型及真实 SDK 装配/执行与既有回归共 31 项通过；Ruff、架构守卫及定向代码审查通过。本机 Worker 空闲后重启，真实模型任务 `71921a0cf11f49aa90264dd088c5b61f` completed，运行配置核对为 200,000，API health 200。此验证不等于已向提供商发送 200K 长输入做容量验收。本机证据：`.venv/f089-context-compaction-tests.log`、`.venv/f089-context-200k-runtime.log`、`.venv/f089-context-worker-proof.json`。

## 已验证

200K 默认阈值的后续真实单会话压力验证见 [上下文压缩实测](./context-compaction-validation-2026-10-19.md)：单次模型输入真实超过 262K，确认摘要状态、历史归档与标签保留；同时发现并过滤内部摘要混入执行流的问题。200K 为触发阈值，保留的大消息仍可能令压缩后输入超过该值；长历史检查点重建耗时问题另行记录，不计为已解决。

| 范围 | 证据与结果 |
|---|---|
| AC-01–AC-07、AC-20–AC-21 | 正文 delta/snapshot/end、同长快照、终态迟到帧、重放 epoch、批量更新、精确最终正文去重有自动回归；浏览器主正文在生成中增长，两个后台成员具有独立 invocation，长输出及计时持续推进 |
| 协作显示 | 用户会话只读核对确认两名内置成员 namespace 分开；修复主工作插入后第二成员失去分组，以及正文被挪到工具记录前面的问题。独立样例页面按成员编号和工具/正文顺序显示 |
| AC-08–AC-12、AC-14–AC-18 | 真实 DOCX 嵌图、表格、编号重启；PDF 中文与图片代表页；工具出口真实生成与“另存为”两种格式均实际下载并解析成功 |
| AC-13、AC-20 | owner/admin、未授权用户、跨租户、有效/错误/过期分享、伪造他任务文件引用的允许/拒绝配对；集中经过 PermissionService；当前是服务级与端点回归，不冒充真实双租户 HTTP E2E |
| AC-19 | 真实 XLSX/PPTX/CSV/图片解析；伪文件和损坏 XLSX worksheet 被拒绝；最终文件核验结果随版本保存。没有增加任意格式互转 |
| AC-22–AC-24 | 有界批量、慢存储不阻塞片段；缓存和资源变化失效；远端 Redis 真实 2 执行/4 等待容量；Windows 取消后代进程和实际解析超时测试、Linux 独立进程组清理 |
| AC-26–AC-29 | Windows 原生与 Linux Docker 实际图文转换、资源路径、字体和解析；Windows Job Object、Linux 进程组，独立 LO profile。Linux 容器远端 Redis 网络集成未通过，详见性能报告 |

使用当前开发登录，在本机前后端和远端存储/数据库上新建 `e2e-f089` 合成任务。没有重新执行、覆盖或修改用户提供的两条原始会话与报告。该浏览器验证不满足“专用测试租户”的完整自动化 E2E 约束，因此明确记作手动集成验证。

保留两条成功合成会话供复核：`a812d65f6df248f98cae815c1e970fa7`（真实报告工具/另存为）和 `8ae31e86e3b0494d93925877bbcc8374`（并行正文/计时）。截图在本机忽略目录 `src/backend/.venv/f089-evidence/collaboration-fixed.jpg`。

后续成员折叠修复：整个成员输出共用展开入口，完成后默认收起，运行时默认展开且保留手动选择。针对流式最终正文去重、完成切换、独立展开与计时的两套组件测试共 13 项通过；客户端 lint、workspace typecheck 和三语 i18n 检查通过。

浏览器在保留的协作样例中实测：完成成员默认收起；展开成员 #1 后 #2 仍收起，再次点击 #1 后正文从 DOM 卸载。截图：`src/backend/.venv/f089-evidence/member-output-collapsed.jpg`。

报告核验后续修复：只读检查 `f68a8ceb487e4537b116b896769cd872`，确认后端导出已成功，随后沙箱因缺 pdftoppm 按旧系统提示安装 Poppler，并多次全页转图。合成样例复现 loose list 产生前导换行，修复后 Windows/Linux LibreOffice 真实转换均通过；序号与正文纵坐标差均为 0，中文、嵌套/续段、项目符号、图片和表格已人工查看。样例与渲染图在忽略目录 `src/backend/.venv/f089-evidence/list-fixed-{Windows,Linux}.{pdf,png,json}`。相关渲染、导出工具、提示兼容、runtime 与既有回归共 54 项通过；没有覆盖或重跑该用户任务。新核验指引属于提示约束，尚不声明已有真实模型回合遵循新版指引。

13:45 确认本机旧 worker 的两个 owner 记录均为 completed、私有队列为空后，仅重启该工作区已核实的 worker 进程树。13:46 新 worker 心跳正常，API health 200；新版提示用于之后新建的执行图，不追改原任务历史。

## 检查结果

- 后端完整 `test/linsight/` 与分享下载回归：1062 通过、13 跳过（包含真实格式解析/超时用例）。
- 客户端定向：8 套、153 项通过。全量 Jest：68 套通过、5 套失败，592 项通过、6 项失败；从 Git HEAD 导出的干净前端副本运行同 5 套，得到相同 5 套/6 项失败，确认是既有问题。
- 前端 workspace typecheck、客户端 lint、i18n parity 通过。workspace lint 仍有未修改 platform 文件的 54 个既有 any 错误；没有增加或手改 suppressions。
- 架构检查没有 VIOLATION；两条既有 endpoint 直接导入模型的 WARNING 保留。Ruff 对修改前后按规则/消息比较，不增加违规。

## 尚未验收

2026-10-19追加完成8个正常UI真实任务（7次成功、1次缺失旧技能包在模型前失败），集中调整并复测：覆盖短答、并行核验、含图PDF、选中技能、Word、公式Excel与成员编辑菜单。详细问题清单、失败的性能复测与最终结果见 [real-task-validation.md](./real-task-validation.md)。最终文档任务68.859秒，未再安装大型Office；成员模型菜单透明背景及Excel数字格式丢失已实测修复。此补充不替代以下尚未验收矩阵。

AC-25 的硬件/浏览器/资源完整性能矩阵，真实专用测试租户中的双身份 HTTP 访问链路，Linux 容器到远端 Redis 的网络配置。已知验证限制详见 [performance-report.md](./performance-report.md)。本次没有部署共享服务器、合并或提交代码。

## 重启后的最终复核与清理

- 最后一次合成任务经 write_file → export_docx/export_pdf → 隔离核验 → MinIO 收集完成，共 3 个产物。另存为 PDF 实际下载 82638 字节、1 页；渲染页核对中文、编号和表格正常。
- 软删除 5 条本次前缀匹配且已终止的合成测试会话，保留前述 2 条成功样例；原会话与报告保持只读。
- API /health、3001 前端、4001/workspace 均返回 HTTP 200，本机 worker 已重启使用最终代码。
- 临时 Git HEAD 前端副本及其 node_modules 目录链接的清理被自动审批策略拒绝（blocked by policy）；没有改用其他工具绕过，保留在本机 .venv/f089-frontend-baseline 与对应 zip。该目录被忽略，未提交；原依赖没有删除。
