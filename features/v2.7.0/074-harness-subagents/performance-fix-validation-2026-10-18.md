# Harness 性能修复验收（2026-10-18）

范围：用户在性能审查后授权“全部处理一下”，落实审查中的四项问题。记忆排除；只在本地修改、迁移、测试和提交，没有推送远端。

## 实现与兼容

1. 客户端：思考按 call_id/epoch 索引合并，同文本的重复增量仍逐字保留；新代次替换旧生成。快照在独立 draft 内回放后一次提交历史状态，不对每个 token 更新 Recoil。实时路径保持不可变。`started_at` 保留原始耗时。
2. 快照：原始格式默认保留；客户端选择 `compact=true`。Lua 只读首尾边界，XRANGE 每次最多 512 条，尾游标固定。连续同调用/同代次思考无损合并，其余事件保持顺序。分页期间追加留给 WebSocket；裁剪/失效显式返回不完整。
3. 恢复扫描：只读 id、tenant_id、recovery_state，每批 256 条，以 id 游标分页；`status/recovery_epoch` 筛选，pipeline 批量检查租约；原 scheduled NX、租约及 DB fencing 保持。新增 `(status,id)` 索引，模型与迁移一致。
4. 成员资源校验：模型、最小工具/类型联表、严格租户下的选定启用 Skill 名称并行读取；同组动作按组去重，复用现有批量权限服务。没有跨步骤权限缓存；每次模型/工具调用都重新检查禁用、删除与授权。

## 同机测量

探针执行真实 TypeScript helper、实际 EventReplayService、API/数据库和 MemberResourceGuard；下表不是生产容量或整体任务延迟。

| 路径 | 修改前 | 修改后 |
|---|---|---|
| 2 万 thinking 增量投影，中位数 | 3204.56ms | 10.61ms |
| 同一 3438 事件真实任务快照 | 1,397,349 bytes / 3438 事件 | 30,195 bytes / 10 事件，减少 97.84% |
| 上述 API 三次耗时 | 209.87 / 161.62 / 180.12ms | 509.68 / 98.79 / 132.52ms；首次在服务重启后且与其他测量并行，不隐去离群值 |
| 同一 2042 事件任务快照 | 832,630 bytes | 21,054 bytes |
| 91 事件短任务 API | 22.44–38.69ms | 44.63–51.27ms；分批读取和完整性校验有固定开销，不能保证短任务提速 |
| 2 万个 400 字符增量的合成快照 | 11,210,336 bytes；379.82 / 451.13 / 372.03ms | 8,000,462 bytes；343.56 / 393.19 / 374.51ms |
| 上述合成快照 JSON 序列化 | 39.57ms | 9.30ms |
| 恢复扫描 EXPLAIN | ALL / key=NULL | ref / ix_linsight_session_version_status_id |
| 真实成员：1 工具、1 连接器、2 Skills，热校验 | 10 SQL；32.06–33.55ms | 9 SQL；23.18–28.82ms |
| 同一成员首次校验 | 12 SQL / 254.67ms | 11 SQL / 247.62ms |

合成快照每条 400 字符的正文必须全部保留，因此合并后仍约 8 MB；Redis 持久日志体积不变。分批读取消除整段历史 Lua 占用，整个请求仍是 O(N) 解码，无法承诺固定响应大小或耗时。真实思考 token 的元数据占比高，压缩收益更明显。

## 真实服务与浏览器

- 本地 `alembic upgrade head` 成功：`f088_agent_member_default -> f074_recovery_scan_index`；`alembic heads` 只有新 head，实际 EXPLAIN 命中新索引。使用已有双数据库反射 helper；本次没有真实 DM8 实例验证。
- 3438 帧任务：两次真实 WebSocket 连接覆盖 3437 个后续事件，缺失 0、重复 0；无效游标返回 replay_reset。
- 完成、恢复、成员、终止四个真实任务：原始/压缩快照的全部 thinking 文本、起止时间、全部非 thinking 事件与末尾游标完全相同，分别为 3438→10、1851→12、229→16、314→14 事件。
- 新无成员任务 `863a88deac474debb337b4bae642ad96`：持久 write_file 后强杀专用测试 Worker，重启后 epoch=2，自动恢复一次并 completed；旧事件 ID 全部保留，最终标记命中。
- 新成员任务 `4cc2ca2157e84aa0869cda76e69ccf30`：lib-10 读取所选 Skills；连接器真实返回 42；E2B 代码工具真实返回 stdout=10、error=null，主 Agent 汇总 PERFORMANCE-MEMBER-OK。此次代码工具从开始到结束 84 秒，属于尚未专项优化的外部沙箱路径；不能把前述毫秒指标解释为整体任务完成时间。
- 浏览器新开临时页验证长任务完整 200 行、恢复提示与产物、新恢复任务 60 行及最终标记、成员独立卡片和上述两个真实结果。临时标签已关闭，用户原标签保留。

## 自动化与质量门禁

- 前端：6 套件、107 测试通过；回放/游标/终止/步骤树回归通过；新增 2 万帧单次历史提交、重复文本保留、原状态不变和耗时保留测试。
- 后端：最终单独运行 `test/linsight`：985 passed、8 skipped（含新索引测试）；新测试覆盖固定边界下并发追加、跨页合并、历史裁剪、分页扫描、活租约跳过、调度去重、组级批量权限、下一次调用立即撤权及模型/工具/组/Skill 禁用。
- Client lint、全前端 typecheck、check-i18n 通过；后端增量 ruff 无新增诊断。逐改动文件运行 arch-guard，无 VIOLATION；旧 linsight API 直接导入 models 的 RULE-3 WARNING 保留，未增加导入。
- 扩展运行 `test/linsight test/database`：1079 passed、8 skipped、1 failed（新增索引测试加入前）。失败为未改动的 `test/database/test_f020_migration.py::test_upgrade_creates_composite_unique_index`，单跑同样失败；其连接桩仍模拟旧 INFORMATION_SCHEMA 查询，而实际 helper 已使用索引反射。该历史测试不属于本次性能代码修改。
- 全前端 lint 仍为 platform 54 项历史 no-explicit-any；此次未修改 platform 或扩大抑制基线。

## 证据与回退

原始/修改后性能证据：忽略目录 `src/backend/.venv/f074-evidence/performance-*.json` 与 `performance-after-*.json`；探针位于 `.venv/f074-tools/f074-performance-*`。真实恢复和成员任务证据为 `review-1018-performance-*`，截图 `performance-long-replay.jpg`、`performance-member-browser.jpg`，日志 `.venv/f074-logs/performance-*`。

压缩为可选参数，可先回退客户端；后端仍支持原始回放。恢复扫描、资源校验可独立回退；索引提供 downgrade，代码回退可保留无害索引。未放宽沙箱进程状态、外部未知副作用、超次数、缺失回执等恢复阻断规则。未做数小时、多租户高并发和沙箱冷启动专项压测。
