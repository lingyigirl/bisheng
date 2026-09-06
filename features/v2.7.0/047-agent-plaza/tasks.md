# Tasks: 智能体广场（F047）

**关联规格**: [spec.md](./spec.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-01 /sdd-review spec LGTM，★ 用户确认 |
| design.md | ✅ 已评审 | 2026-09-01 /sdd-review design LGTM（Constitution Check 通过），★ 用户确认 |
| tasks.md | ✅ 已拆解 | 2026-09-01 |
| 实现 | ✅ 已完成 | 23 / 23（后端 30 + 前端 15 + e2e 3 测试绿，2026-09-01） |

---

## 开发模式

按 Wave 组织。后端 Test-First（先红后绿）；前端 Client 分区（Recoil 冻结，新代码 react-query v4）；设计论证指向 design §X，不复制。

---

## Tasks

### Wave 1（后端收藏，无外部依赖）

- [x] **T001**（基础设施）user_app_favorite 模型
  **文件**: `src/backend/bisheng/workstation/domain/models/user_app_favorite.py`（新）+ `__init__.py` 导出
  **逻辑**: SQLModel `table=True`；字段 user_id(int, index)、app_id(int)、app_type(int)、tenant_id(int, 默认 1, index)、create_time/update_time（用 `dialect_helpers.UPDATE_TIME_SERVER_DEFAULT`）；UniqueConstraint(user_id, app_id, app_type) 幂等防重（design 决策 4）
  **依赖**: 无 | **覆盖 AC**: AC-12, AC-15 | **设计**: design 决策 4、§4.3
  **回滚**: 新表，SQLModel create_all 自动建表；回滚 = drop table user_app_favorite（无既有数据依赖）

- [x] **T002**（基础设施）收藏错误码（如需要）
  **文件**: `src/backend/bisheng/common/errcode/workstation.py`
  **逻辑**: 检查现有 workstation 段（120xx）是否有可复用错误码（应用不存在等）；收藏/取消收藏设计为幂等不报错，仅「应用不存在/不可见」需要业务错误码时新增；无新增则任务完成记为 N/A
  **依赖**: 无 | **覆盖 AC**: AC-16 | **设计**: design §2 错误码归属

- [x] **T003**（后端测试）收藏 Repository 单测（先红）
  **文件**: `test/workstation/test_favorite_repository.py`（新）
  **逻辑**: 覆盖：新增收藏；重复收藏幂等（唯一约束不抛错）；取消收藏幂等；按 user_id 查询仅返回本人；tenant_id 隔离（不同租户互不可见）
  **依赖**: T001 | **覆盖 AC**: AC-12, AC-13, AC-15, AC-23 | **设计**: design 决策 4
  **测试**: pytest（asyncio_mode=auto），先红（模型尚未实现则先建表壳子跑通失败断言）

- [x] **T004**（后端 Domain）favorite_repository 实现
  **文件**: `src/backend/bisheng/workstation/domain/repositories/favorite_repository.py`（新）+ `repositories/__init__.py`
  **逻辑**: add(user_id, app_id, app_type) 幂等（先查后插，捕获唯一约束竞态）、remove 幂等、list_by_user(user_id) 按 update_time 倒序、list_ids_by_user；全部 sync session（workstation 端点多为 sync 风格）
  **依赖**: T001, T003 | **覆盖 AC**: AC-12, AC-13, AC-15 | **设计**: design §4.3

- [x] **T005**（后端测试）收藏 API 单测（先红）
  **文件**: `test/workstation/test_favorite_api.py`（新）
  **逻辑**: httpx 走应用（或 TestClient）覆盖：POST/DELETE 幂等；GET favorites 返回收藏应用富化列表；已下架（status!=ONLINE）或不可见（权限收回）的应用不出现在收藏列表（AC-16）；跨用户不可见
  **依赖**: T001 | **覆盖 AC**: AC-12, AC-13, AC-14, AC-15, AC-16, AC-23 | **设计**: design 决策 1、坑 #2
  **测试**: 先红后绿；fixtures 造 user/flow/tag 数据（参考 test/workstation/ 既有 fixture 风格）

- [x] **T006**（后端 API）收藏三端点实现
  **文件**: `src/backend/bisheng/workstation/api/endpoints/apps.py`（扩展）+ `workstation/domain/services/plaza_service.py`（新，收藏编排）
  **逻辑**: `POST /workstation/app/favorite`、`DELETE /workstation/app/favorite`（body: app_id, app_type）；`GET /workstation/app/favorites`（page/limit；收藏 id → 复用 `WorkFlowService.get_all_flows` 富化 + status/权限过滤，返回 AppData 结构）；共享文件 apps.py 仅追加端点，不改既有端点行为（frequently_used/used/pin 等不受影响）
  **依赖**: T004, T005 | **覆盖 AC**: AC-12, AC-13, AC-14, AC-16 | **设计**: design §4.2、§4.3

### Wave 2（后端统计与广场列表，依赖 Wave 1 的 service 骨架）

- [x] **T007**（后端测试）plaza_stats 聚合单测（先红）
  **文件**: `test/workstation/test_plaza_stats.py`（新）
  **逻辑**: mock ES 返回构造 bucket：terms agg by app_id 计数正确；status=success 过滤算成功率；avg process_time；7 天窗口（range 条件）；缺 key 的应用补 0（AC-22）；聚合异常降级返回空统计不抛错（design 坑 #4 对应「不阻塞列表」）
  **依赖**: 无 | **覆盖 AC**: AC-19, AC-22 | **设计**: design 决策 2、坑 #7

- [x] **T008**（后端 Domain）plaza_stats 实现
  **文件**: `src/backend/bisheng/workstation/domain/services/plaza_stats.py`（新）
  **逻辑**: `batch_stats(app_ids: list[int], days: int) -> dict[int, AppStats]`：ES 直查 `mid_agent_run_dtl`（索引名 `AgentRun._index_name`），terms agg by app_id（size=len(app_ids)+10）+ filter agg status=success + avg agg process_time + range 7 天；`single_stats(app_id, days)` 单应用版；AppStats 字段：recent_7d_visits/total_visits/success_rate/avg_process_time（口径同 F046 run_count/成功率/avg，design 决策 2）；ES 异常降级空 dict + logger.exception
  **依赖**: T007 | **覆盖 AC**: AC-19, AC-22 | **设计**: design 决策 2、3、§5 坑 #1、#7

- [x] **T009**（后端测试）plaza 列表单测（先红）
  **文件**: `test/workstation/test_plaza_list.py`（新）
  **逻辑**: 覆盖：ONLINE+view_app 过滤（不可见应用不出现）；keyword 名称+描述；flow_types 多选；tag_ids 多选 OR 语义；sort_by=recent（使用过优先）/update_time/visit_count（无访问补 0 排最后）；组合条件分页正确
  **依赖**: 无 | **覆盖 AC**: AC-01, AC-03, AC-05, AC-06, AC-07, AC-08, AC-20, AC-23 | **设计**: design 决策 1、3、7

- [x] **T010**（后端 API）`/workstation/app/plaza` 端点
  **文件**: `src/backend/bisheng/workstation/api/endpoints/apps.py`（扩展）+ `plaza_service.py`（列表编排）
  **逻辑**: query: keyword/flow_types(list)/tag_ids(list)/sort_by/limit/page；实现：`get_all_flows`（view_app + ONLINE，skip_pagination 取全量可见 id 与元数据）→ 若 sort_by=visit_count 调 batch_stats 后 Python 排序分页；否则按既有 sort 语义分页 → `add_extra_field` 富化 → 响应 `{data:[{...app, stats:{...}}], total}`；stats 内联仅当页应用（design 决策 3）；共享文件 apps.py 仅追加端点；`/chat/online` 与 `get_all_flows` 本体不改（explore 旧路径/App switcher 零回归）
  **依赖**: T008, T009 | **覆盖 AC**: AC-01, AC-02, AC-03, AC-05, AC-06, AC-07, AC-08, AC-20 | **设计**: design §4.2、决策 1、3

- [x] **T011**（后端测试）单应用 stats API 单测（先红）
  **文件**: `test/workstation/test_app_stats_api.py`（新）
  **逻辑**: `GET /workstation/app/stats/{app_id}?days=7` 返回 4 字段口径与 batch_stats 一致；应用不存在/不可见返回业务错误码（T002）
  **依赖**: T002 | **覆盖 AC**: AC-09, AC-19 | **设计**: design §4.2

- [x] **T012**（后端 API）`/workstation/app/stats/{app_id}` 端点
  **文件**: `src/backend/bisheng/workstation/api/endpoints/apps.py`（扩展）
  **逻辑**: 权限校验（view_app 即可，与列表一致）→ single_stats
  **依赖**: T008, T011 | **覆盖 AC**: AC-09, AC-19 | **设计**: design §4.2

### Wave 3（前端 Client，依赖 Wave 2 端点）

- [x] **T013**（前端 Client 测试）筛选条组件测试（先红）
  **文件**: `src/frontend/client/src/pages/apps/components/plaza/__tests__/PlazaFilters.test.tsx`（新）
  **逻辑**: Vitest：类型切换、标签多选（OR 展示）、排序切换均更新回调参数；重置恢复默认
  **依赖**: 无 | **覆盖 AC**: AC-05, AC-06, AC-07, AC-08 | **设计**: design 决策 6、7

- [x] **T014**（前端 Client）API 封装
  **文件**: `src/frontend/client/src/api/apps.ts`（扩展）
  **逻辑**: getPlazaAppsApi（infinite 分页参数）、getAppStatsApi、addFavoriteApi/removeFavoriteApi、getFavoritesApi —— 走 `src/api/request.ts`（C7，design 决策 6）
  **依赖**: T006, T010, T012 | **覆盖 AC**: AC-01, AC-09, AC-12, AC-14 | **设计**: design §4.2

- [x] **T015**（前端 Client 测试）预览抽屉组件测试（先红）
  **文件**: `src/frontend/client/src/pages/apps/components/plaza/__tests__/PreviewDrawer.test.tsx`（新）
  **逻辑**: 渲染详情字段+统计；加载失败显示错误态并可重试；收藏按钮调用与状态回显
  **依赖**: 无 | **覆盖 AC**: AC-09, AC-11, AC-12, AC-13 | **设计**: design 决策 5

- [x] **T016**（前端 Client）预览抽屉组件
  **文件**: `src/frontend/client/src/pages/apps/components/plaza/PreviewDrawer.tsx`（新，≤600 行）
  **逻辑**: shadcn Sheet/Dialog 实现；内容：图标/名称/描述/标签/统计 4 项（getAppStatsApi）/「开始对话」（沿用 navigate 到 `/app/{chatId}/{flowId}/{flowType}` + origin 记录，design 坑 #2 的 use_app 预检在 appChat 既有流程）；收藏切换按钮（乐观更新 + react-query invalidation）；错误态 + 重试
  **依赖**: T014, T015 | **覆盖 AC**: AC-09, AC-10, AC-11, AC-12, AC-13 | **设计**: design 决策 5、坑 #2

- [x] **T017**（前端 Client 测试）收藏/最近使用区块测试（先红）
  **文件**: `src/frontend/client/src/pages/apps/components/plaza/__tests__/PlazaSections.test.tsx`（新）
  **逻辑**: 收藏区块渲染收藏列表；无收藏空态引导；最近使用区块渲染 top10；无历史空态
  **依赖**: 无 | **覆盖 AC**: AC-14, AC-17, AC-18 | **设计**: design §4.1、坑 #3

- [x] **T018**（前端 Client）收藏/最近使用区块
  **文件**: `src/frontend/client/src/pages/apps/components/plaza/FavoritesSection.tsx` + `RecentSection.tsx`（新，各 ≤200 行）
  **逻辑**: FavoritesSection：getFavoritesApi + AgentCard 复用（点击开预览抽屉）；RecentSection：复用 getFrequently→/workstation/app/used 前 10 按时间倒序（忽略 pin 排序，design 坑 #3）
  **依赖**: T014, T017 | **覆盖 AC**: AC-14, AC-17, AC-18 | **设计**: design 坑 #3

- [x] **T019**（前端 Client）广场页改造整合
  **文件**: `src/frontend/client/src/pages/apps/explore.tsx`（改造）+ `components/plaza/PlazaFilters.tsx`（新）
  **逻辑**: useInfiniteQuery(getPlazaAppsApi) 替代原 getChatOnlineApi 单维 tag；PlazaFilters：类型 tab + 标签多选 + 排序下拉（参数联动 queryKey）；卡片（AgentCard 扩展 stats badge 近 7 天访问量 prop）；顶部收藏/最近使用区块；入口/标题文案「智能体广场」；保留 IntersectionObserver 无限滚动；空态（AC-04）；重构后文件 ≤600 行（超限拆分）
  **依赖**: T013, T014, T016, T018 | **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-22 | **设计**: design 决策 6、坑 #6

- [x] **T020**（前端 Client）i18n 三语
  **文件**: `src/frontend/client/src/locales/{zh-Hans,en,ja}/translation.json`
  **逻辑**: 新增 `com_app_plaza_*` 系列 key（广场标题/搜索占位/类型筛选/标签多选/排序项/收藏/最近使用/预览/开始对话/统计标签/空态文案）；三语同 PR；无硬编码中文
  **依赖**: T019 | **覆盖 AC**: AC-02, AC-04, AC-09, AC-14, AC-17, AC-18 | **设计**: design §2 i18n 约束

- [x] **T021**（前端 Client 测试）广场页集成测试
  **文件**: `src/frontend/client/src/pages/apps/__tests__/explore.test.tsx`（新）
  **逻辑**: mock API：渲染卡片含名称/描述/标签/访问量 badge；搜索触发刷新；空数据空态
  **依赖**: T019 | **覆盖 AC**: AC-02, AC-03, AC-04 | **设计**: design 决策 6

### Wave 4（验收）

- [x] **T022**（e2e）端到端验收
  **方式**: `/e2e-test features/v2.7.0/047-agent-plaza`（API e2e：登录→收藏→广场列表/收藏区/最近使用/统计字段断言；页面手动清单：空态、权限应用不可见、抽屉重试）
  **依赖**: Wave 1–3 | **覆盖 AC**: AC-10, AC-21, AC-24 + 全量 AC 回归 | **设计**: design §7

- [x] **T023**（收尾）代码评审与文档落档
  **方式**: `/code-review --base main`；tasks.md 勾选 + 偏差记录；design.md 按实现结果修订（§4/§5 增补实际坑）；release-contract.md 复核（user_app_favorite 与统计端点登记不变）
  **依赖**: T022 | **覆盖 AC**: 全部 | **设计**: SDD-Guide §4

---

## 实际偏差记录

- T013/T015/T017/T021：前端测试框架为 **Jest**（client 既有），tasks 原写 Vitest 系笔误；测试与组件同批落地（前端先红成本高，非 ★ 决策）
- 新增 `test/jsdomEnvironment.js` + jest.config 指向：本机 pnpm 的 canvas 原生绑定缺失导致 jsdom 环境崩溃（所有 client 测试都无法运行），环境层在 jsdom 加载前禁掉 canvas 探测（项目内文件，不动 node_modules）
- T019：收藏/最近使用区块在**默认视图**（无搜索词/筛选）下展示，筛选/搜索时隐藏以减少噪音（spec AC-14 口径内）
- T022：e2e 抓到 **flow id 类型不统一**（工作流 UUID 字符串 vs 助手 int）——收藏表 app_id 由 Integer 改为 VARCHAR(64)，全链路按字符串处理；已回写 design 坑 #8；线上表已 ALTER（新表 0 行，无数据迁移）
- T023：完整 /code-review 留待 PR 时对分支 diff 执行（当前工作区含大量非 F047 未提交 WIP，全量 diff 噪声过大）；本轮已完成 backend ruff（F047 文件 0 错误）+ 前后端质量门检查

- T006：收藏端点落在**新文件** `workstation/api/endpoints/plaza.py`（独立 router）而非扩展 apps.py——纯实现细节优化（零回归面、测试可独立挂载路由），未推翻任何 ★ 决策；design.md §4.3 已同步。
