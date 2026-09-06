# F047 智能体广场 — Spec Discovery 记录

> 调研日期 2026-09-01。本文记录 spec 动笔前的现状调研与关键决策点（★ 已由用户确认的决策以 ✅ 标注）。
> 决策依据进入 spec.md；实现细节在后续 design.md。

## 1. 现状调研结论（可复用 vs 需新建）

### 前端 client（/workspace）

| 能力 | 现状 | 结论 |
|---|---|---|
| 展示已发布应用 | `/apps/explore`（`src/pages/apps/explore.tsx`）已有 banner + 关键词搜索 + 单维标签 tab + 无限滚动卡片（`AgentCard`），数据源 `GET /api/v1/chat/online`（permission_id=view_app） | **升级该页为完整广场**（保留路由与入口） |
| 搜索 | 服务端 `keyword`（名称/描述）+ 客户端过滤 | 复用，补描述搜索开关已存在 |
| 分类筛选 | 仅单维 `tag_id` tab（`/api/v1/tag/home`） | 需扩展为多维度 |
| 收藏 | **无**（App Center 只有 pin 置顶 / frequently_used 自动常用；`mark_*` 是标注子系统无关） | **新建** |
| 历史 | `/apps`（App Center）基于 `GET /workstation/app/used`（最近使用，pinned-first）；应用内会话历史已有 | 广场加「最近使用」区块即可复用 |
| 统计展示 | **无任何访问量/使用量展示** | **新建**（数据源已有） |

### 后端

| 能力 | 现状 | 结论 |
|---|---|---|
| 应用列表 | `GET /api/v1/chat/online` 已支持 keyword/tag_id/flow_type/page/limit/sort_by(used-first/update_time)/search_description/permission_id；v2 仅有 assistant/list（无 workflow） | 筛选维度后端大多已具备 |
| 发布口径 | `status=ONLINE` + ReBAC 权限过滤；**无** on_public/上架开关 | 见决策 D1 |
| 收藏 | **无**（`UserLink` 表模式可仿，需补 tenant_id） | 新建表 + API |
| 最近使用 | `MessageSessionDao.get_user_used_apps` + `GET /workstation/app/used` | 直接复用 |
| 访问统计 | `application_process` 遥测事件（每次应用运行一条：app_id/app_name/app_type/status/process_time）→ `mid_agent_run_dtl` 中间表（5 分钟同步，F046 已建）；**无对外读取端点** | 新建只读聚合端点（es 直查或复用 mid 表） |
| 权限 | viewer≠use_app：广场展示按 `view_app`（与 explore 现状一致），进入会话按 `use_app` | 沿用，不改权限模型 |

## 2. 关键决策点（★ 全部已确认 ✅）

- **D1 上架口径** ✅ 沿用 ONLINE+view_app（无新字段，无上架开关）
- **D2 多维度筛选维度** ✅ 类型 + 标签多选 + 排序（最近使用/最新/最多访问）
- **D3 预览形态** ✅ 卡片点击 → 详情预览抽屉 → 进入会话
- **D4 统计口径** ✅ 卡片访问量 badge + 预览层统计 + 按访问量排序（不做排行榜区块）

## 3. 已定（推荐口径，未获用户异议即入 spec）

- 页面形态：升级现有 `/apps/explore` 为「智能体广场」，不新建路由；入口文案由「探索」改为「智能体广场」
- 智能体口径：工作流 + 助手两类（与 F046 口径一致；灵思/频道文章/知识空间不进广场）
- 收藏：新建 `user_app_favorite`（user_id + app_id + app_type + tenant_id，仿 UserLink 模式），广场顶部「我的收藏」区块 + 卡片收藏按钮；与 App Center 的 pin/常用 并存不冲突
- 历史：广场「最近使用」区块复用 `GET /workstation/app/used`，不改应用内会话历史
- 统计数据源：`mid_agent_run_dtl`（F046 中间表）；口径：累计访问次数、近 N 天访问、成功率、平均响应时间（同 F046 指标定义）
- 权限：列表 view_app、进入 use_app（与现状一致，不新增权限位）
