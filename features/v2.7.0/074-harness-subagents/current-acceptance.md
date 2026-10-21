# Harness 当前验收摘要

更新时间：2026-10-21。本文为最新结论入口；历史逐轮报告保留原始环境、提交和测试数字。文档索引见 [README](./README.md)，停机与本机重启说明见 [本地环境交接](./local-environment-handoff.md)。

## 状态

- 最新 Harness 全流程复测基于 `3bed230fc` 及本地兼容修复；没有合并 main。
- 2026-10-21 复测期间本地 API、Linsight Worker 和两端 Vite 正在运行，使用云端中间件；复测结束后 Worker 保持运行。
- 核心场景通过，仍有已知问题与覆盖边界，T36 完整上线验收不勾选。记忆按约定排除。

## 已验证能力

| 能力 | 最新证据 |
|---|---|
| 无成员 Harness、自主计划和工具循环 | [完整本地验收](./full-local-acceptance-2026-10-18.md)、[无成员补测](./memberless-retest-2026-10-18.md) |
| 云端 E2B、联网搜索、工作区文件与产物下载 | [完整本地验收](./full-local-acceptance-2026-10-18.md) |
| 自定义协作人员的名称、提示词、模型、Skills、工具、连接器 | [完整本地验收](./full-local-acceptance-2026-10-18.md) |
| 主 Agent 路由、后台委派、结果回收与整合 | [推送前回归](./prepush-validation-2026-10-18.md) |
| 两个 DeepSeek 模型配置主/子互换 | [多模型与外部副作用补测](./multimodel-external-recovery-2026-10-18.md)；不是跨厂商验收 |
| 流式反馈、游标补播、人工输入、继续、主动终止 | [完整本地验收](./full-local-acceptance-2026-10-18.md) |
| 外部完成回执复用、未知结果阻断、不重复发送 | [多模型与外部副作用补测](./multimodel-external-recovery-2026-10-18.md)；不是所有业务连接器完整 E2E |
| Skill 对象存储、缓存重建、迁移及权限 | [Skill 运维手册](./skill-object-storage/runbook.md)、[权限补齐](./skill-object-storage/openfga-repair-2026-10-18.md) |
| 并行子代理流式积压优化 | [性能修复](./stream-buffer-performance-2026-10-18.md)：6000 段模拟写入减少 99.8%；真实双模型长输出约 32 秒完成 |
| 2026-10-21 全流程复测 | [Harness 全流程复测](./harness-end-to-end-retest-2026-10-21.md)：自主规划、E2B、错误后调整、刷新重连、暂停恢复、Worker 重启续跑、工具与 MCP 连接器、主/子 Agent 整合均通过 |

内置研究员、分析员、图表与质检角色和用户协作人员并存。内置角色继承主模型，自定义成员按快照解析各自模型。主 Agent 收尾前须收集必要后台结果；任务停止、异常及未知副作用遵循 [恢复策略](./recovery-policy.md)。

## 最新检查

2026-10-21 复测：

- 后端 Linsight：1126 passed、16 skipped。
- Client Harness：22 suites / 245 tests passed。
- 前端 typecheck、client lint、i18n 通过。
- Platform ESLint 仍有既有 54 个 `no-explicit-any`，不属于本次 Harness 回归。

以 [推送前回归](./prepush-validation-2026-10-18.md) 为最终执行记录：

- 后端 Linsight + 单 Alembic head + 相关权限/迁移：1121 passed、13 skipped。
- Harness 前端：21 suites / 233 tests passed。
- 真实 HTTP、云端 Redis 与进程故障恢复专项：37 passed。
- 新增双子代理完成后重建/整合前中断检查点测试及相关定向回归：44 passed。
- 前端工作区 typecheck、客户端 lint、i18n、增量架构检查通过。

上述测试范围有重叠，不相加为总通过数；未运行或未通过的全站检查不能写成通过。

## 未解决与边界

| 项目 | 当前结论与下一步 |
|---|---|
| 旧长任务自动接续无结果 | 2026-10-21 未复现。简单 Worker 重启续跑任务没有规划子任务和 `TaskEnd`，因此按设计走 direct-answer fallback 并正常收集文件；该日志目前为 WARNING，建议后续按“是否存在真实规划任务”区分 INFO/WARNING，避免监控误报 |
| 一次本地 API 异常退出 | 故障注入后发生，原因未确认；后续复测未重现，见 [完整本地验收](./full-local-acceptance-2026-10-18.md) |
| 5 个旧业务 Skill 缺源 | 需找回真实源包或重新导入；对象存储迁移不能从元数据生成原文 |
| 全站存量质量问题 | 客户端完整测试 5 个失败套件、管理端 54 项 any lint 未处理；与当前 Harness 定向通过分开记录 |
| 覆盖边界 | 长期记忆未接入；跨厂商、多物理机、生产级长时并发、所有外部工具副作用和平台 DM8 全量验收未完成 |
| 沙箱恢复 | 文件可恢复不等于 Python 变量、后台进程或连接状态可恢复；必要时安全阻断，不承诺任意崩溃自动续跑 |
| 成员测试约定 | 自定义成员的 Skills、工具和连接器列表彼此独立；要求成员使用某工具前必须确保该成员实际配置了该工具 |
| 连接器稳定性 | 知识产权 MCP 流程查询曾出现一次 `context canceled`，同调用重试成功；需要继续观察远端数据库连接稳定性 |

## 历史材料

原 2026-10-14 摘要已归档为 [新基线历史验收](./baseline-acceptance-2026-10-14.md)，其中“仅本地未推送”、本地副本数据库和早期迁移 head 只代表当时环境。2026-10-18 后续验证为本机应用连接云端中间件，最新已实测迁移 head 为 `f035_skill_object_storage`；详见 [云端启动迁移验证](./remote-startup-migration-validation-2026-10-18.md) 与 [完整本地验收](./full-local-acceptance-2026-10-18.md)。
