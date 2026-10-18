# Harness 本地功能性能检查

2026-10-18，基线 `14597eda1`。本轮为性能审查和有限测量，未修改产品实现、数据库结构或权限。结论：当前小任务正常，但发现 1 项高优先级与 3 项中优先级扩展性问题；功能回归通过不能证明这些路径满足长时/并发性能要求。

## P1：前端流式历史与快照补播存在平方增长

位置：`client/src/hooks/Websocket/recoveryProjection.ts:18`；`Websocket/index.tsx:435`；`hooks/useLinsightManager.tsx:47`（以上位于 src/frontend 下）。

`appendRecoveryStep` 每帧对历史执行 filter 并展开复制，即使都是同一次执行代次也遍历全部旧帧。快照加载对每条事件单独 applyEvent，并分别进入状态更新；N 条连续流式帧需要约 N(N-1)/2 次过滤检查。React 批处理不能消除函数式状态更新中的这部分数组运算。

直接将当前 TypeScript helper 转译并在独立 Node v24.9.0 中执行；每组 3 次，模拟同 call_id、同 epoch 的思考流，保留实际 helper 逻辑：

| 原始帧数 | 投影耗时中位数 | 过滤检查数 |
|---|---:|---:|
| 1,000 | 4.24ms | 499,500 |
| 5,000 | 107.80ms | 12,497,500 |
| 10,000 | 468.22ms | 49,995,000 |
| 20,000 | 3,204.56ms | 199,990,000 |

这些是数据处理基准，不是浏览器页面加载实测；未包含 Recoil、React 渲染和 DOM，因此不能把数字直接当作网页耗时。但已确认长历史投影本身有秒级同步开销风险，尤其刷新和切换长任务时。

建议：快照在局部 accumulator 一次投影后提交状态；按 call_id/epoch 建索引，避免每帧完整过滤；实时帧合并到有界批次。必须保留游标仅在应用成功后推进、代次替换、澄清与终态一致性。不要只减少 React render 次数而保留所有历史扫描。

## P2：快照没有分页或尺寸上限，单次读取全部事件

位置：`src/backend/bisheng/linsight/domain/services/event_replay.py:26,84`；快照 API 在 `linsight/api/endpoints/linsight.py:850`。

Lua 使用 XRANGE '-' '+' 返回全部事件，服务端解析全部 JSON，API 再整体序列化返回。当前上限是事件条数 100,000，未按字节限制；每次刷新/首次打开任务都会重复读取。不能把流式 WebSocket 的每批 100 条理解成快照也分页了。

真实本地 API，各任务连续读取 3 次，包含鉴权、数据库读取、Redis、序列化和本地 HTTP；不包含浏览器投影：

| 任务事件数 | 响应字节数 | 三次请求耗时 |
|---:|---:|---|
| 91 | 40,132 | 38.69 / 22.44 / 36.50ms |
| 2,042 | 832,630 | 163.81 / 158.70 / 104.78ms |
| 3,438 | 1,397,349 | 209.87 / 161.62 / 180.12ms |

另使用独立临时 Redis stream、每帧 400 字符合成数据调用真实 snapshot 方法，测试后删除专用键：

| 事件数 | JSON 序列化后字节 | snapshot 三次耗时 | 额外 JSON 序列化 |
|---:|---:|---|---:|
| 10,000 | 5,605,130 | 205.43 / 196.15 / 199.94ms | 18.96ms |
| 20,000 | 11,210,336 | 379.82 / 451.13 / 372.03ms | 39.57ms |

合成结果排除 HTTP、鉴权和数据库，不能作为并发吞吐结论，也没有测量 Redis 服务端独占执行的准确时长。风险是响应体、峰值内存及前端处理随历史增长，叠加上一项平方开销。

建议：使用紧凑执行投影加增量游标，或采用带固定快照边界的分批读取，并定义字节预算。必须保证投影与游标同源，处理读取期间裁剪，不能简单分页后把最后游标取为实时尾部而丢事件。

## P2：恢复扫描缺少索引，并加载完整运行中任务

位置：`src/backend/bisheng/linsight/domain/repositories/recovery_repository.py:45`；`domain/services/recovery_service.py:132`；每个 Worker arena 的启动路径都会创建 monitor。

实际 MySQL 表有 129 行；运行中查询 `EXPLAIN SELECT * FROM linsight_session_version WHERE status='IN_PROGRESS'` 显示 type=ALL、possible_keys/key 均为空，估计扫描 111 行。实际索引仅覆盖 id、session_id、tenant_id、user_id，没有 status 索引。

扫描每 5 秒发起一次，先读取完整 ORM 行，再在 Python 过滤 recovery_protocol；完整行包含 question、agent_config、output_result 等大字段。对每个候选再串行 EXISTS 查询租约，多 Worker 重复执行这套发现过程。当前小表不是已观测到的慢查询，但历史任务增长后，即使运行任务很少仍会全表扫描。

建议：通过新的单链迁移评估 status 索引；Repository 提供最小候选字段投影、限量/游标批次，Redis 租约检查 pipeline 化；评估统一发现器或对扫描进行分片。任务领取仍需原子租约与数据库代次校验，不得靠减少扫描绕过并发隔离。MySQL/DM8 均需检查索引与执行计划。

## P2：成员每个模型/工具步骤重复全套资源校验

位置：`src/backend/bisheng/linsight/domain/services/configured_subagents.py:23`；`agent_config_service.py:49,65`。

真实取此前任务的 lib-10 成员快照（1 个代码工具、1 个连接器、2 个 Skills），使用同租户/用户和实际数据库、权限服务执行 MemberResourceGuard.validate，并通过 SQLAlchemy Engine 事件计数；没有调用模型或执行工具：

| 样本 | 校验耗时 | SQL 条数 |
|---|---:|---:|
| 首次 | 254.67ms | 12 |
| 后续 1 | 33.55ms | 10 |
| 后续 2 | 32.46ms | 10 |
| 后续 3 | 32.06ms | 10 |

模型前、工具前都会完整执行该校验。作为量级计算，20 次步骤会带来约 200 条校验 SQL；这不是完整任务耗时或生产并发实测。启用的 Skills 目录会被反复查询；同一工具组的多个动作还会逐动作调用权限入口。

建议：在单次校验中复用模型/工具组/权限基础元数据，合并所需资源查询，按组选定权限去重，避免枚举无关 Skills。跨步骤复用必须具备可信版本或失效机制，保持已验证的撤权/禁用及时阻断语义，不能用一个长 TTL 权限缓存直接跳过检查。

## 排序、影响与验证边界

建议先处理前端投影，再处理快照体积，随后优化扫描和校验查询。修改前两项预计降低长任务刷新/回放的同步计算与传输量；后两项降低长期积累和并发下的数据库、Redis 请求数。快照/游标与权限校验的正确性风险高于单纯 UI 变化，应分别提交并覆盖补播不丢不重、恢复代次、停止与撤权回归。索引变更需要单独迁移；本次未实施这些建议。

已存在的有效控制：思考历史数据库写入已按 2 秒合并；job_output 为异步有界等待；WebSocket 使用阻塞读取和游标补播；无成员任务不查询成员工具与技能目录。没有把这些已经受控的路径误报为无节制轮询。

本次没有数小时/多租户并发压力测试、真实浏览器 20,000 帧渲染测量或完整 E2B 文件同步性能测量，不能据此给出生产容量承诺。无需重新执行产品单测：产品代码没有改变，本次运行的是定向测量。

原始证据在忽略目录 `src/backend/.venv/f074-evidence/performance-{client-projection,real-db-api,synthetic-snapshots,member-guard}.json`；探针在 `.venv/f074-tools/f074-performance-*`，日志在 `.venv/f074-logs/performance-*`。没有修改业务任务、权限或数据库结构，临时 Redis stream 已清理。
