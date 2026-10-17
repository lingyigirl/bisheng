# F074 实施结果

> 最新状态：2026-10-14 已完成新基线真实 E2B 文件分析/下载、双成员、补播、人工等待点重启恢复及撤权复测。152 项后端回归、5 项真实环境测试通过。以 [current-acceptance.md](./current-acceptance.md) 为准；下文早期环境限制保留为历史记录。

日期：2026-10-13。分支：`feat/v2.7.0/074-harness-subagents`。

## 已实现

- 工作空间任务模式增加协作成员抽屉，支持名称、职责、提示词、模型、Skills、工具和已注册连接器动作。配置作为任务快照保存，运行中只读。
- deepagents 原生子 Agent 分别装配模型、工具和技能；并行上限 2，成员上限 5。每次委派通过实际调用 ID 和子图 namespace 关联，不再按顺序猜测。
- 执行时间线增加成员卡片：目标、状态、结果、活动详情。失败事件有独立 outcome，终止时不继续显示正在执行。
- 新模式使用 Redis Stream 非破坏性订阅、幂等写入和原子事件快照。客户端按事件 ID 去重；断网恢复后补播；超期/丢失记录明确降级到可用历史。
- 成员调用前重新校验模型、技能与工具权限。任务流与快照在读取前校验用户和租户归属；共享页面保持脱敏历史查看。
- 新模式代码执行要求 E2B，不会静默落到本机执行。成员技能在创建工具前复制，E2B 绑定任务工作区，清理路径释放成员沙箱资源。
- 长期记忆提供 MemoryAdapter 接口和可注入实现；默认明确 unavailable，不建立重复的记忆存储，也不声称已完成 8.2/8.3。
- 数据库迁移：`f074_agent_config`，父节点为实际查询到的 `f064_field_comment_override`，新增 nullable JsonType 字段；使用 DM8 兼容的 column_exists 检查。

## 已运行的检查

| 检查 | 结果与范围 |
|---|---|
| 后端专项回归 | 前轮 137 passed；2026-10-14 受影响 16 文件 132 passed，范围不同不相加 |
| 新增真实环境专项 | 5 passed；真实 Redis、API、WebSocket 与已完成的真实模型双成员任务 |
| 前端 Jest | 6 suites / 98 passed（增加分享路由只读与所有者控制） |
| 本轮故障相关后端回归 | 82 passed；含 Worker 清扫、租户定向失败通知、人工恢复、事件补播与实际图 |
| 全 frontend `pnpm lint` | 通过；仅通过 lint:prune 缩减已有 suppressions |
| 全 frontend `pnpm typecheck` | 通过 |
| `pnpm check-i18n` | 通过，无新增漂移；11071 从共享文案源生成 |
| Alembic 单 head / MySQL 迁移 | 真实 MySQL 升级至 f074_agent_config；JSON 字段存在，重复升级退出 0；DM8 待验 |
| Python 格式与静态检查 | 新代码无新增 Ruff 诊断；修改的历史文件仍有 408 项既有诊断（本轮包含 Worker 与旧分享测试文件），不宣称全量 clean |
| 架构规则 | 修复了触及入口原有的跨模块 API 依赖；剩余 Endpoint 旧模型导入为历史 WARNING |

后端测试覆盖：严格配置、独立装配、真实 deepagents 图并行委派、重建图后人工中断恢复、实际 Lua 协议、HTTP 快照访问与非所有者拒绝、记忆适配、技能目录、旧 Worker/事件/E2B 文件协议与分享回归。

说明：原 137 项回归中的图/Redis/E2B 使用假模型、fakeredis/lupa 和离线替身。后续另起独立 Docker 环境，使用用户提供的 DeepSeek 模型完成双成员任务、真实 WebSocket 断线补播和人工等待点重启 API/Worker 后恢复；详细证据见 [e2e-report.md](./e2e-report.md)。E2B 仍只有离线证据。

## 尚未完成的环境验收

- Docker 中间件、API 7860 与独立 Worker 已启动，主链路通过真实联调并保持运行。
- 已通过正常登录完成页面核心流程；实际结束 API 后验证浏览器持续退避重连，真实 Worker 崩溃和主动终止均显示终态。新增修复：仅重试 3 次后放弃、Worker 清扫只改 DB 不发事件。详见联调报告。
- 暂停后禁用子模型的真实恢复阻断已验证，测试后已恢复上线。真实 Skill/连接器执行、工具撤权/Skill 禁用、跨租户 API、共享页面只读和浏览器历史裁剪提示已通过。修复独立 Worker 缺少 OpenFGA 初始化及分享父组件漏传只读状态；详情见联调报告。
- 未提供 E2B 配置，云端代码执行与文件回传待验证；DM8 迁移待验证。
- 长期记忆真实提供者待 WBS 8.2/8.3 对接。
- 未实现任意 Worker 崩溃后的自动安全续跑；人工等待点换 API/Worker 进程后已通过真实 checkpoint 恢复，不扩大为任意故障安全重放承诺。

## 部署顺序

1. 在测试环境执行 `uv sync --frozen --extra test --python 3.11`，提供环境配置。
2. 执行 `uv run alembic upgrade head`；在 DM8 中验证新增字段。
3. 部署 API 和 Linsight Worker，再部署 client。配置代码解释器为 E2B。
4. 按 [e2e-checklist.md](./e2e-checklist.md) 完成真实验收后再合并发布。

回滚应用版本时保留新增 nullable 字段；DDL downgrade 会删除配置快照，不能作为日常回滚操作。
