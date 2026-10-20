# 执行状态

| 文档 | 状态 | 说明 |
|---|---|---|
| spec.md | ✅ 已评审 | 2026-10-19 用户确认；随后直接补充 Linux/Windows 兼容要求 |
| design.md | ✅ 已评审 | 用户“那继续任务吧”授权按已讨论方案进入实施；保留两平台与无 Chromium 客户环境约束 |
| tasks.md | ✅ 已拆解 | 按 tasks-checklist 自评，测试先于配对实现，逐波检查 |

## 实施任务

路径均相对仓库根目录。每项检查后再标完成；最终以实际验证结果为准。

- [x] T001（测试）正文混合帧、快照去重、身份与回放：`src/backend/test/linsight/test_assistant_text_stream.py`。覆盖 AC: AC-01–AC-07、AC-20。配对 T002。
- [x] T002（后端 Domain，依赖 T001）新增 `assistant_text_mapper.py`，接入 mapper、coalescer、步骤持久化与 replay_compaction；`task_exec.py` 接入协议与最终段身份。
- [x] T003（前端 Client，依赖 T002）`hooks/Websocket/recoveryProjection.ts` 与测试；提取批量 reducer，增加正文渲染，接入执行树/结果去重；兼容能力标记及三语。覆盖 AC: AC-02–AC-07、AC-20–AC-21。
- [x] T004（测试）`src/backend/test/linsight/test_report_export_render.py`：真实图文、编号、表格、越界/坏资源、格式完整性。覆盖 AC: AC-08–AC-19、AC-27。配对 T005。
- [x] T005（后端 Domain，依赖 T004）common 纯报告渲染器与资源解析，严格资源处理；`tool/domain/services/` 提供真实格式核验。
- [x] T006（测试）`test/linsight/test_report_runtime.py`：子进程超时/取消、缓存、并发和失效。覆盖 AC: AC-22–AC-24、AC-26、AC-28–AC-29。配对 T007。
- [x] T007（后端 Domain，依赖 T006）共享导出服务、受控 worker、平台适配、有界执行与结果复用。
- [x] T008（后端 API，依赖 T005/T007）`linsight_export.py`、`report_export.py`、既有另存为端点及包装 API；授权后解析工作区，入口一致失败语义。覆盖 AC: AC-08–AC-18。
- [x] T009（后端 Domain，依赖 T005/T007）既有产物收集接入一次性核验、Markdown/HTML 资源引用与错误产物处理。覆盖 AC: AC-12、AC-17、AC-19、AC-23。
- [x] T010（验证，依赖 T002–T009）后端定向回归、前端 lint/typecheck/i18n、架构检查、任务级审查；消减触及文件的冻结问题。
- [ ] T011（E2E/性能，依赖 T010）独立样例验证双入口、浏览器流式与重连、实际 Linux/Windows 文档生成和生命周期；固定性能样例前后对照。覆盖 AC: AC-01–AC-29。
- [ ] T012（审查，依赖 T011）code-review、修复审查问题、更新 design/e2e-report/性能报告与任务状态；保留实际限制，不伪报通过。

## 实际偏差记录

- 实际模块、PDF auto 回退、后台持久化与队列 scope 修复见 design.md §9。
- T010 完成定向/完整后端回归与客户端检查；仓库级 pnpm lint 被未改动 platform 文件的 54 个已有 any 错误阻挡，不新增 suppression。
- T011 已执行真实浏览器双入口、并行正文/计时和 Linux/Windows 图文与生命周期验证；实际信创浏览器、禁 GPU/heap、完整并发压力与专用双租户 HTTP 套件仍未执行，性能验收为 PARTIAL。
- T012 已完成本地七维审查并记录结果，但发布/合并门禁随 T011 与仓库已有质量问题保持未关闭；未提交、未合并、未部署远端。
- 追加8轮真实任务与一轮集中调整/复测，过程中同步消除工具说明冲突并最终复测；记录15类问题及残留边界，见 [real-task-validation.md](./real-task-validation.md)。修复沙箱首字前预热、缺包旧技能可选、PDF含图页误报、成员下拉菜单透明背景、Excel格式丢失；待办更新及污染成员配置等未伪报关闭。
