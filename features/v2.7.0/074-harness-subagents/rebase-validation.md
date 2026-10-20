# 新基线恢复与回归

> DM8 更新（2026-10-14）：已启动独立实例，F074 新增字段的迁移、重复升级、大文本同步/异步读写专项通过。详见 [dm8-validation.md](./dm8-validation.md)。下文“无 DM8 环境”为此前状态，整个平台 DM8 全量验收仍未完成。

> 最新状态：2026-10-14 已完成新基线真实 E2B 文件分析/下载、双成员、补播、人工等待点重启恢复及撤权复测。152 项后端回归、5 项真实环境测试通过。以 [current-acceptance.md](./current-acceptance.md) 为准；下文早期环境限制保留为历史记录。

日期：2026-10-14。目标分支：feat/2.7.0/beijing-ns-llm-guardrail，基线 f1b49c51f。

## 恢复范围

从本地备份恢复原有 85 个文件的功能增量。已有文件三方合并；7 处清单/语言冲突按旧基线到 Harness 的实际增量处理，保留新基线新增内容。新增文件不存在路径冲突。没有远端写入。

适配修改：
- F074 迁移接到目标单 head f064_field_comment_override。
- 时间线新增 intent 节点明确返回非运行态，新增回归测试。
- 三个相关组件测试显式导入 jest-dom，使 matcher 类型不依赖偶然的全局导入。
- uv/pnpm 按目标锁文件同步，新增 sqlglot 与 DB2 驱动已安装，另将锁文件已有的 @types/testing-library__jest-dom 5.14.9 声明为 Client 直接开发依赖，以适配 pnpm 的类型解析；无运行时依赖升级。
- lint 豁免仅用 lint:prune 自动缩减。

## 检查结果

- 后端 Harness、Worker、分享、事件映射与离线 E2B 文件协议：132 passed。
- Alembic 单 head 检查：1 passed；alembic heads 仅 f074_agent_config。
- 前端成员配置、补播、时间线、任务卡：7 suites / 103 passed。
- frontend 根目录 typecheck：platform、client、file-viewers 全部通过。
- check-i18n：通过；共享错误码产物通过构建检查。
- 修改的 Client 文件 lint：通过；新增 Python Ruff 通过、既有 Python 无新增诊断。
- 架构守卫：无 VIOLATION；旧 Endpoint 模型导入 WARNING 保留。
- 全量 frontend lint 未通过：platform 54 项、client 5 项。全部位于与目标 HEAD 完全一致的五个文件：platform 的 controllers/API/{dashboard,finetune,workflow}.ts，client 的 pages/apps/__tests__/explore.test.tsx 和 test/jsdomEnvironment.js；不新增豁免、不将这些既有错误记作通过。

## 环境边界

本轮为新基线代码恢复和回归测试，未把旧进程的 API 响应记作新代码联调结果。此前本地数据库已执行旧父链的 f074_agent_config；不能直接认为新父链中间迁移已执行，后续真实联调应使用新建测试库按完整迁移链初始化，或在备份后单独制定测试库对齐步骤。未运行破坏性降级。

E2B、DM8 与真实记忆服务仍待环境/其他负责人；前一轮真实模型与权限证据见 e2e-report.md。本轮没有远端提交、推送或 MR 操作。
