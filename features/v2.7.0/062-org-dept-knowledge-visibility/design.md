# Design: 机构/部门标记与树形知识库可见范围（062-org-dept-knowledge-visibility）

> **本文档定位 — 现状快照（Why this How）**
>
> - `spec.md` 回答 **做什么**（目标、AC、边界）
> - `design.md`（本文）回答 **为什么这么实现**：关键决策、运行时不直观的事实、对外契约
> - `tasks.md` 是 **流水账**：拆了哪些任务、做了什么改动

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-21

---

## 1. 目标与非目标

- **目标**：在组织树上引入「机构 / 部门」二值标记，并据此把组织知识库的部门派生可见性从 F057 的「只向下」扩展为「机构域内纵向直通 + 横向互通」。规则仍是**读时求值、不写 FGA 元组**，因此组织调整（改名、移动、增删部门）不需要回算任何授权。
- **非目标**：不做例外机制（deny / 排除名单）、不做可见集合的物化缓存列、不让知识空间参与本规则、不做标记的父子继承。

---

## 2. 关键约束

- **全局铁律遵循 `docs/constitution.md` C1–C7**（DDD 分层 / 双 DB / 多租户 / 权限统一入口 / 错误码 / 无硬编码密钥 / 前端 store 不直连 HTTP），本节不重抄。
- **本特性特有的约束**：
  - **判定位于热点路径**。`_implicit_department_library_level` 在两条高频链路上被逐行调用：① 知识库列表的逐行权限检查（`_scan_visible_knowledge` 按 50 行/批扫描+补页）；② 部门角标 `department_stats` 对**全部**有归属的知识库做权限收口。新规则比 F057 多一次「向上回溯到最近机构」的遍历，若实现为逐次 DB 查询会产生 N×深度 的查询量。
  - **组织树规模**：本特性**按单租户 500–1000 个节点设计**（约 90 个机构；目标客户规模，用户提供 2026-09-21）。
  - **⚠ 但本仓代码自证存在大得多的部署**：`department_service.py:1518` 的注释明写「超管下会把全部 **3w+ 部门**加载进内存」，`:1519` 进一步指出达梦 async 驱动序列化**数万** bind param 是该接口的主瓶颈。这两个数字说明**单租户节点数在本仓的已知上限比目标客户大一个数量级**，且已有专门绕开全量加载的优化。因此：「整表载入」只在本特性的目标规模下成立，设计必须显式限定适用范围，并为大租户留退化路径（见决策 6 / 决策 7）。
  - **租户隔离是硬约束**：任何缓存/映射的**键必须含租户**。多租户下部门 id 在不同租户间可能重复，跨租户共享的 `id → nearest_org_path` 映射会让租户 A 的判定命中租户 B 的机构路径，直接构成跨租户越权（违反 C3）。
  - **DM8 兼容**：新增列走 `dialect_helpers`，`ALTER TABLE` 的风险与约束沿用 F057 迁移的既有结论（见 `v2_7_0_f057_knowledge_department.py` 模块 docstring）。
  - **上游数据形态依赖**：`department.path` 是物化路径 `/1/2/3/`，**最后一段是节点自身 id**（`_parse_path_ids`；`department_service.py:1512` 的注释是陈旧的，说反了）。整条规则建立在这个语义上。

---

## 3. 方案对比与选定

### 决策 1：标记的语义是「是否对上级透明」，不是「机构 / 部门」这个名字

- **备选**：
  - A. 按字面语义实现：机构 = 一种节点类型，部门 = 另一种，规则按类型分派。
  - B. 把标记定义为**一个布尔语义**——「该节点是否对其上级透明」——名字沿用业务词汇「机构 / 部门」。
- **选定**：B
- **原因**：业务词汇是入口，但落进代码必须能回答「这个字段影响什么」。A 会让每一处消费点各自解释「机构意味着什么」，一旦某处理解不同就出现口径分裂。B 让规则可以一句话说清（见决策 3），且让**默认值的选择**变得有据可依（不透明 = 安全缺省，见决策 2）。
- **何时该重新考虑**：若将来出现第三种节点类型（如「虚拟组织」）且其可见性语义不是「透明 / 不透明」二分。

### 决策 2：存量一律回填「机构」，且这是**升级安全性的唯一保证**

- **备选**：
  - A. 存量回填「部门」— 全租户互通，银行合规不可接受。
  - B. 按层级猜（根 = 机构、其余 = 部门）— 只要根是机构，`domain(根)` 就吞掉整棵子树（没有任何墙），仍然全租户互通。
  - C. 存量全部「机构」（`server_default 0`），由实施按需标注。
- **选定**：C
- **原因**：**全部标为「机构」时，新规则精确退化成 F057 的现状**（每个节点的域只有它自己，可见集 = 自身 + 全部祖先 = `owning.path` 是用户 path 前缀）。这不是近似，是恒等——已在 spec 中以 AC-02（升级后逐条相同）和 AC-20（全机构退化为 F057 口径）双向钉住。
  A 和 B 都会在升级瞬间把历史数据整体开放，且**不可回滚**——没有地方能审计「升级后那几分钟谁看到了什么」。
- **代价（必须向上说明）**：本特性**上线后不会自动生效**，客户必须先完成组织树标注才会观察到需求所述效果。标注属交付实施动作，写入实施清单与验收前置条件（spec §已知缺口）。
- **何时该重新考虑**：无。这是安全属性，不是权衡项。

### 决策 3：判定式 = 「最近机构祖先的路径是用户部门路径的前缀」

- **备选**：
  - A. 枚举「用户可见的部门集合」，再判 `D ∈ 该集合`。
  - B. 对每个 D 求 `nearestOrg(D)`，判其 `path` 是否为用户某个所属部门 `path` 的前缀。
- **选定**：B
- **原因**：A 需要把机构域整棵子树展开成 id 列表，在 1000 节点的规模下每次请求都要展开、还要处理缓存；B 只需两次路径运算，且**与 F057 的现有判定只差一个词**——F057 是 `D.path` 作前缀，F062 是 `nearestOrg(D).path` 作前缀。实现上是同一个比较，左边从 `D` 换成 `nearestOrg(D)`。
  这条判定式已写进 spec §2.2 作为**唯一口径**，§2.2 的各条 AC 都是它在不同树形上的表现，不得各自另立解释（spec AC-25 进一步要求 F057 回写的 AC-03a/03b 也引用该口径）。
- **何时该重新考虑**：若组织树深度出现极端值（如 >20 层），回溯成本上升，需回到决策 6 重新评估。

### 决策 4：机构是唯一屏障；部门可无限层穿透（包含关系，不是层级带）

- **备选**：
  - A. 层级带：只有与用户「同级别」的部门才横向可见。
  - B. 包含关系：机构域内**所有**部门互相可见，不管中间隔了几层部门。
- **选定**：B
- **原因**：A 会让可见性随「中间隔了几层部门」变化——`信贷研发` 直接挂在根下就可见，多包一层 `信贷研发部` 就不可见。这在产品侧无法向用户解释，且会逼业务调整组织架构去迁就权限。B 让规则只剩一条：*打开一个机构，域内全部收进来，只在遇到下一个机构时停下*。
- **何时该重新考虑**：若客户提出「同机构内也有需要层级隔离的条线」——那说明需要的是第三种节点类型，而不是修改本规则。

### 决策 5：嵌套机构构成边界（下级看不到下级机构域）

- **备选**：
  - A. 机构域 = 该机构整棵子树（含嵌套机构及其全部后代）。
  - B. 机构域 = 该机构子树，但**遇到嵌套机构即剪枝**（含该嵌套机构节点本身）。
- **选定**：B（对应 spec AC-12 / AC-12a）
- **原因**：只有 B 让标记在**每一层**都产生效果。A 之下二级支行的标记不隔离任何东西——它和同层的普通部门没有区别，字段在这一层等于没标。且业务措辞把「下级部门」与「下级机构」分开枚举，说明二者被区别对待。
- **已知副作用（用户已确认接受）**：带来**上下不对称**——下级能看见上级的部门，上级看不见下级的机构域。这是 B 的定义所蕴含的，不是缺陷。
- **注意剪枝点**：「含该嵌套机构节点本身」是易漏项。判定式里的 `nearestOrg(D)` 对 D 本身就是机构时取 D 自身，正是为了剪掉它（否则 A 的语义会从后门溜回来）。spec 为此单列 AC-12 与 AC-12a 两条。

### 决策 6：部门表进程级 TTL 缓存，而非逐次回溯查询

- **备选**：
  - A. 每次判定沿 `parent_id` 向上走，遇 `is_org=0` 即止 —— 最坏 O(深度) 次 DB 查询。列表 50 行/批 × 深度 5 = 250 次查询/批；角标对**全部**归属库执行 = 数千次查询。
  - B. 每请求开始时一次性载入租户部门表（id / parent_id / path / is_org），在应用层算出 `id → nearest_org_path` 并沿调用链下传。
  - C. **按租户**的进程级 TTL 缓存 + **懒加载**同一份映射，部门树变更时显式失效。
- **选定**：**C**（A 不可接受，B 因侵入性被否）
- **原因**：
  - A 在目标规模下仍会产生量级过大的查询（角标路径尤甚），直接否决。
  - B 需要把参数穿过 `PermissionService.get_implicit_permission_level`（定义 `permission_service.py:970`）→ `_implicit_department_space_member_level`（定义 `:1158`，调用点 `:1082`）→ `_implicit_department_library_level`（`:1185`）。**实测调用面**（`grep`，2026-09-21 二次核实）：`get_implicit_permission_level` **3 处**、`_implicit_department_space_member_level` **1 处**、`_implicit_department_library_level` **1 处**。三个调用点分别落在 `permission_service`（内部）、`fine_grained_permission_service`（2 处）、`resource_permission` 端点（1 处）——**跨 3 个模块，且不共享请求作用域**，参数没有共同的传递路径。为一个特化的性能需求改动公共签名并给每个入口补上下文，收益仍不匹配风险。
    > **勘误（两次）**：本节初稿写「59 处调用方」——错，那是 codegraph blast radius 里**另一个符号**（`PermissionService.check`）的数字。首轮更正为「9 / 1 / 4」，**仍不准**——那仍是从 blast radius 输出读的，不是 `grep` 实测。**实现期 grep 实测为 3 / 1 / 1**。结论（选 C）不受影响，但它说明一件事：**blast radius 的数字不等于调用点数量，写进决策依据前必须 grep 核实**。<br/>**对决策的实际影响**：调用面比原先设想的更小（3 处而非 9 处），「侵入性」这个否决理由相应更弱；选 C 的真正理由回到 TTL 失效语义的正确性与实现简洁性上，见本节开头的三条硬性要求。
  - C 的规模安全（**限定在目标规模内**）：单租户 500–1000 行，全量载入是廉价操作；映射算好后判定是 O(1) 字典查。
  - **缓存失效已有既有模式可循**：`PermissionCache.invalidate_all()`（`permission_cache.py:143`）已在本次决策 10 中被引入调用。
- **必须的处理（三条，缺一不可）**：
  - **① 键必须含租户**（C3 硬约束）。缓存结构为 `{tenant_id: {dept_id: (parent_id, path, is_department)}}`（**整行元组**，理由见 §4.3 末尾）。**绝不允许跨租户共享同一份映射**——命中错误租户的树即跨租户越权。这是本决策的**门禁项**，实现与测试都必须覆盖。
    > **对初版理由的更正**：这里原写「部门 id 跨租户可能重复」——**不成立**。`department.id` 是自增主键（`database/models/department.py:52`），**全局唯一**；跨租户重复的只有**业务键** `dept_id`。租户维度**仍然必要**（载入本身就必须按租户过滤，且 `Department` 有 `tenant_id` 列参与 C3 自动过滤），但理由不是「id 会撞」，而是「不按租户载入就没有正确的树」。
  - **② 懒加载 + 容量上界**。不预热、不做全局扫描；首次命中某租户时才载入该租户的映射。这样在多租户部署下，即使某租户有 3w+ 节点，也只有**被访问到的**租户才付出成本。
    > **实现状态：部分兑现（已知短板）**。②原写「设每租户条目上限」，**实际未实现**：上限只以 `MAX_CACHEABLE_DEPARTMENTS`（单租户超限则整体拒缓存）的形式存在，**缓存自身没有容量上界、也没有跨租户淘汰**；过期项只在**该租户再次被访问**时 pop，此后不再被访问的租户条目会永久驻留。在「租户数 × 树规模」上单调增长。已记入 §8。
  - **③ 超大租户的退化路径**。若某租户节点数超过 `MAX_CACHEABLE_DEPARTMENTS`（5000，约为目标规模上限的 5 倍），**不做全量载入**，退回按 `parent_id` 逐级回溯（方案 A）。理由：该规模下全量载入正是 `department_service.py:1519` 记录的主瓶颈（达梦 bind param 序列化），不能在本特性重蹈。退化路径只需正确、不需快——它服务的不是本特性的目标客户。
    > **实现状态：已实现，但少了一样东西**。原写「并带请求内 memo」，**实际没有 memo**：超阈值租户下，列表路径仍是 `50 行/批 × 深度` 次查询/批，正是本决策否决方案 A 的那个量级。**功能正确**（这也是为什么它不阻塞本特性交付），但大租户下性能等于方案 A。已记入 §8。
    > **退化路径必须与缓存路径同答案**（坑 15）：它是降级不是放宽，分歧方向尤其不能偏「允许」。
  - **陈旧窗口**：缓存过期前，部门树变更对本规则不可见。靠 TTL 兜底（建议与 `PermissionCache.TTL = 10s` 同量级），并在部门**新增 / 改名 / 移动 / 归档 / 标记翻转**五类变更处显式失效。
  - **§5 #10 是这条决策的反面证据**：F057 曾因「缓存部门链」被否（在**每请求**粒度上否得对）。本决策是**进程级（按租户）+ 显式失效**，不是在请求内缓存一次就算数——两者的失效语义不同，不可用 F057 的结论直接否决本条。
- **何时该重新考虑**：若**目标客户**的单租户节点数增长到 5000 以上，退化路径会变成常态路径，需改为按租户分片 + 增量维护；若部门树变更频率显著上升（如 SSO 高频同步），改为在同步完成钩子中失效。

### 决策 7：候选集用超集，不做精确枚举

- **备选**：
  - A. 精确算出用户的可见部门 id 集合并下推 SQL（`department_id IN (上千个 id)`）。
  - B. 候选集取「全部有 `department_id` 的库」，精确性交给逐行判定。
- **选定**：B
- **原因**：`aget_user_knowledge` 的 `department_ids` 参数**只是候选集加宽**（`knowledge.py:363-371` 的 OR 分支），真正的判定发生在随后的逐行 `get_knowledge_permission_map_async`。因此候选集**只需是可见集合的超集**。在目标规模（500–1000 节点）下超集带来的额外扫描可忽略，而 A 会引入大 IN 列表（DM8 参数上限风险，见 `department_service.py:1519` 记录的 bind param 序列化瓶颈）和每次请求的部门表查询。
- **规模边界（与决策 6 ③ 同源）**：本方案的代价是**扫描量**而非正确性，因此它在目标规模下安全、在 3w+ 规模下不安全。若单租户节点数超过决策 6 的阈值，候选集也会同步失控——届时应改用 A 或引入决策 6 的映射做 SQL 下推。**两条决策的阈值必须一致**，不要各自设定。
- **何时该重新考虑**：若节点数增长到 5000 以上，超集扫描会明显拖慢首页（用户实际可见的库少、候选多时尤甚），届时与决策 6 一并调整。

### 决策 8：规则全局生效，不做入口分叉

- **备选**：
  - A. 只在客户端生效，平台端保持旧口径。
  - B. 全局生效（一处改动，所有消费方跟随）。
- **选定**：B
- **原因**：`_implicit_department_library_level` 是**所有调用方的唯一真源**。A 需要给规则加「调用来源」参数，代价是同一个库在两个界面可见性不同——而 `use_kb` 是检索期用的，会出现「问答时按新规则放行、平台列表按旧规则不显示」的割裂，即「我明明能用，列表里却没有」。这条割裂在 spec AC-17 中已被「view_kb 与 use_kb 一起授予」排除过一次，不应从另一侧再引入。
- **何时该重新考虑**：无。

### 决策 9：只作用于组织知识库（type=0/1），不碰知识空间

- **备选**：
  - A. 同时作用于知识空间。
  - B. 只作用于组织知识库。
- **选定**：B（对应 spec AC-18）
- **原因**：与客户端实际过滤面一致（客户端组织知识库列表 = `type=0` ∪ `use_kb` 过滤）。且知识空间已有独立的成员制授权（`SpaceChannelMember` + `_membership_satisfies_relation`），硬塞部门穿透会制造两套并行语义，在「成员可见 / 部门可见 / 二者冲突」上产生无法解释的组合。
- **何时该重新考虑**：业务明确要求「机构内的知识空间也自动互通」时，需先评估两套授权语义的叠加方式。

### 决策 10：标记翻转与节点移动 = 确认 + 审计 + 全量缓存失效

- **备选**：
  - A. 当普通字段处理，不做保护。
  - B. 二次确认（含影响面数量）+ 审计日志 + `PermissionCache.invalidate_all()`。
  - C. B + 影响清单预览。
  - D. 标记创建时定死、不可翻转。
- **选定**：B
- **原因**：
  - 该字段**不是普通元数据**：翻转会静默改变整棵子树及子树外所有人的可见范围。`部门 → 机构` 是收回，`机构 → 部门` 是放开——后者在银行场景下是合规事故的形状。
  - D 太硬：分行升格为独立机构是常见组织调整，禁止翻转会逼客户走「重建节点 + 迁移成员 + 重挂知识库」的歪路，反而更容易出事。
  - C 的清单在技术上不便宜，且要处理「影响面上千时如何展示」；数量级已足以让操作者停手，具体清单属审计而非编辑弹窗。
  - **缓存失效用 `invalidate_all()` 而非 F057 的「只清直属成员」**：F057 的 `_invalidate_department_members` 刻意不做子树展开（其注释有明确说明），但 F062 的影响人群是**整个子树 + 子树外所有与该域有交集的人**，可能覆盖全租户。用 `invalidate_all()` 是唯一严格正确的选项，且代价有上界——TTL 本就只有 10 秒，最坏情况等于「所有人晚 10 秒过期」。
    > **这是两个缓存，必须都清**（坑 17）：`invalidate_department_mapping()` 清的是**映射**；`PermissionCache.invalidate_all()` 清的是 `PermissionService.check` 已经返回的**判定结果**。只清前者时，翻转**收回**权限的路径上「允许」还会活满 10 秒——正是本决策要消除的 stale-allow。实现期曾漏掉后者（`invalidate_all` 零调用点），已补。
  - **失败方向正确**：spec 决策「接受隐含授权不可撤销」（INV-20）已经排除了负向授权，那这里更不该留一个 stale-allow 窗口——两个决策必须自洽。
- **审计落点（复用既有设施，不新增表）**：写入既有 `audit_log` 表，走 `AuditLogDao.ainsert_v2()`（`audit_log.py:376`）。该表的字段就是为此设计的：`target_type` 的注释已列 `department`（`audit_log.py:142`），`metadata`（JsonType，`:155`）的注释正是「extended fields (**from/to values, affected ids**, etc.)」。落库形态：

  | 字段 | 取值 |
  |---|---|
  | `action` | 新增到部门模块的 action 枚举，沿用既有的 `dept.*` 命名空间（先例：`org_sync/domain/constants.py:34-36` 的 `dept.sync_conflict` / `dept.relink_applied`；枚举类的写法参照 `TenantAuditAction`、`DocExtractAuditAction`） |
  | `target_type` | `department` |
  | `target_id` | 被翻转载记 / 被移动的节点 `dept_id`（业务键） |
  | `metadata` | `{from_is_department, to_is_department, affected_departments, affected_libraries}`（键名与字段名一致，避免读者在两个词之间翻译）；移动另记 `{from_parent_id, to_parent_id}` |
  | `tenant_id` / `operator_tenant_id` | 按 `ainsert_v2` docstring 的 §5.4 规则填（普通用户 → 其叶子租户；全局超管无 F019 scope → `ROOT_TENANT_ID`） |

  **一处需在实现时确认**：`_UI_VISIBLE_V2_ACTIONS`（`audit_log.py:178`）控制哪些 action 出现在「系统操作」审计页。本特性的动作属于高风险权限变更，**建议加入该白名单**（让管理员能查到），但这是产品可见性取舍；无论加不加，`ainsert_v2` 都会落库，合规取证不受影响。改白名单时需与前端 `getActionsApi` / `getActionsByModuleApi` 同步（该元组注释已写明）。
- **已知副作用（已向用户说明并接受）**：`invalidate_all` 的 SCAN pattern 是 `perm:*`，**不带租户前缀**，因此多租户部署下租户 A 的管理员翻转机构会清掉租户 B 的权限缓存。后果仅为 B 多跑一次判定，不涉及数据泄露。若客户要求严格隔离，改为按租户前缀 SCAN 即可（约几行代码）。
  > **注意与决策 6 ① 的区别**：这里跨租户清的是**权限判定结果的缓存**（清了只会重算，无正确性风险）；决策 6 ① 禁止的是**跨租户共享路径映射**（会算错）。两者的严重性不同级，不要因为本条「已经跨租户了」就放松 ①。

### 决策 11：`is_org` 的编辑权限**单独提级**，不与部门编辑权限复用

- **备选**：
  - A. 完全复用现有部门编辑权限（部门管理员可改自己子树内任意字段）。
  - B. `name` / `sort_order` / `default_role_ids` 仍归部门管理员，**只有 `is_org`** 提级到系统管理员 / 租户管理员。
- **选定**：B（对应 spec AC-04 / AC-04a）
- **原因**：A 会留下一条**自下而上的提权路径**——一级分行的部门管理员可以把自己这个节点从「机构」翻成「部门」，效果是解除一道全局屏障，给下面所有人开放原本看不到的内容。他**改的是自己的节点**，但影响范围是全行。系统管理员未必预料到。
  成本几乎为零（`aupdate_department` 内加一个字段级分支），且与风险量级匹配：F057 里同类风险的「把知识库挂到部门」要求 `manage_kb_*`，而翻转的影响面比它大一个数量级。
- **配套（据 spec AC-04a）**：租户管理员的范围与其**既有**部门管理范围一致，不因本特性扩大或缩小。

### 决策 12：不引入任何 deny / 排除机制

- **备选**：
  - A. 接受隐含授权不可撤销，需要隔离时改树结构（标注机构 / 移动节点）。
  - B. 增加知识库级排除（某库屏蔽某些部门）。
  - C. 增加部门级排除。
- **选定**：A（登记为 INV-20）
- **原因**：
  - B / C 都需要在权限评估里引入**减集**语义，而现有整条链路（`get_effective_permission_ids_async` 的并集 → `check` 的五级判定 → 10 秒缓存）都是并集模型。插入减集要同时改缓存语义、失效时机、审计，且极易写出「缓存里存了并集结果、减集规则改了却没失效」这类隐蔽 bug。
  - 本需求要的是「默认放开」，不是「精细到库的例外」。真出现隔离诉求，通常是树结构问题，用「机构」解决正好落在已认可的模型内。
  - 代价是可解释的：任何一个人问「为什么他能看见」，答案都是同一句话——**组织树长这样**。B / C 会让答案变成「树 + 一张例外表」。
- **已知缺口**：单库敏感（某部门的某个库不想给同级看）无法直接表达。变通方式是把该库挂到机构节点下，或把该部门标为机构——两者影响面都可能超出预期。已写入 spec §已知缺口。
- **何时该重新考虑**：若「单库敏感」被证实是高频且刚性的诉求，则必须回到 B，并重新评估缓存与失效语义。

### 决策 13：**不改** `department_stats` 的汇总口径

- **备选**：
  - A. 把汇总从「子树前缀」改为「按本特性的可见集合」。
  - B. 保持「子树前缀」不变。
- **选定**：**B**
- **原因（本条推翻了设计初稿的一个错误判断）**：初稿认为角标会与点击结果脱节，理由是「`domain(总行)` 不含一级分行，但角标会把一级分行整棵子树算进去」。**这个推理混淆了两个方向相反的参数**——
  - 列表的 `filter_department_ids` 是 **subtree 收窄**（F057 AC-09：该部门及其各级下级部门），不是机构域；
  - `department_stats` 的汇总也是 **subtree**（`other.path.startswith(dept.path)`）。
  两者都在这同一个 subtree 上、再套**同一份**逐库权限判定（`filter_knowledge_ids_by_permission_async` vs `get_knowledge_permission_map_async`，二者最终都落到 `get_effective_permission_ids_async`）。因此 **AC-22（角标 = 点击后条数）本来就成立**，F062 改变的是「谁可见」，不是「浏览时按什么收窄」。
  F057 决策 4 早已写明：「归属可见性与平台筛选是两个**相反**的参数，不得合并」——初稿正是把这两个参数合并才得出错误结论。
- **对本特性的实际影响**：`department_stats` **无需代码改动**。它的正确性来自决策 3 对 `_implicit_department_library_level` 的替换；spec AC-22 因此应被理解为**一条需要保持的既有不变量**（F062 可能把它弄坏，所以要断言），而非一项待实现的功能。
- **何时该重新考虑**：若前端把部门筛选从 subtree 改为机构域语义（即 `include_sub_departments` 的语义被重新定义），两者会重新分叉，届时必须同步。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**读（知识库列表）**：`GET /api/v1/knowledge` → `KnowledgeService.get_knowledge`（`knowledge_service.py:347`）分叉：

- **普通用户**：候选集 = `{FGA can_read id} ∪ {本人创建} ∪ {部门链}`（`_resolve_visible_department_chain`）→ `_scan_visible_knowledge`（`:230`）按 50 行/批扫描并对每行跑 `get_knowledge_permission_map_async` → 权限不足的行被丢弃并继续补页。
- **管理员**：`aget_all_knowledge` 直查，跳过逐行判定，权限 map 全量填充。
- **本特性的落点**：两条路径汇入的 `_implicit_department_library_level`（`:1185`）替换规则。候选集与分叉逻辑**不动**。

**读（部门角标）**：`GET /api/v1/knowledge/department_stats` → `KnowledgeDepartmentService.department_stats`（`knowledge_department_service.py:133`）→ 先按权限收口 → 再按 `path` 前缀把每个库累加到其祖先部门。**本特性不改此文件**（决策 13）。

**读（对话框选库）**：客户端组织知识库列表 = `GET /api/v1/knowledge?type=0`（不传 `permission_id`，后端默认 `use_kb`）。随本特性自动跟随，客户端**无代码改动**。

**写（标记）**：`PUT /api/v1/departments/{dept_id}` → `DepartmentService.aupdate_department` → 新增字段级权限分支（决策 11）→ 部门表变更 → 缓存失效（决策 6 / 10）。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `department` 的新增标记列 | 整型，非空，`server_default 0` | `0` = 机构（**不透明**，默认），`1` = 部门（对上级透明）。**语义是「是否对上级透明」，字段名沿用业务词汇**（决策 1） | 可见性判定、组织管理页展示、批量标注 |
| `DepartmentTreeNode`（API 响应） | 既有 pydantic 模型 + 新增布尔/整型字段 | 与既有 `is_tenant_root` / `mounted_tenant_id` 并列。**须给默认值**，否则既有消费方（`_dept_node_dict` 手写 dict 的路径）会因缺字段报错 | 平台组织树、部门选择器、`useLazyDepartmentTree` |
| `GET /api/v1/knowledge` 的 `department_id` / `include_sub_departments` | 整数 / 布尔，可选 | **不含本特性的新语义**，仍是 subtree 收窄（决策 13）。勿与机构域混淆 | platform 知识库管理页 |
| `nearestOrg(D)` | 运行时派生，不落库 | D 自身（D 是机构）或 D 最近的机构祖先 | 判定式（决策 3） |
| `GET /api/v1/departments/{dept_id}/node-type-impact` | HTTP GET，路径用 `dept_id` **业务键**（`BS@...`，须 URL 编码） | **走的是标记翻转的权限门**（与翻转本身同一个检查，不是普通部门读权限）——能翻的人才能问影响面。返回见下行 | platform 确认弹窗（T-21） |
| 影响面响应 | `{affected_departments: int, affected_libraries: int, unavailable?: true}` | **`unavailable` 表示「算不出」，与「算出来是 0」是两回事**：租户超过 `MAX_CACHEABLE_DEPARTMENTS` 时无法评估，前端必须显示为警告而非 0——渲染成 0 会让操作者以为「此操作无害」。两个计数本身可以合法地为 0（翻转一个叶子） | 同上 |
| `DepartmentCreate.is_department` / `DepartmentUpdate.is_department` | 布尔，可选 | 创建时**默认 `False`（机构），不从父节点继承**（AC-03）；更新时 `None` = 不改。改动它单独提级到系统管理员/租户管理员（决策 11） | platform 设置页、创建对话框 |
| `DepartmentDetail.is_department` / `DepartmentTreeNode.is_department` | 布尔（后端 0/1，此处为 bool） | 详情与树节点**两处都要有**：设置页从详情回填，树从节点渲染徽标。`DepartmentTreeNode` 侧须给默认值（`_dept_node_dict` 手写 dict 路径） | platform 设置页回填、树徽标 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `permission/domain/services/permission_service.py::_implicit_department_library_level`（`:1185`） | 读时判定归属库的可见性。**本特性把前缀比较的左侧从 `D.path` 换成 `nearestOrg(D).path`** | 不写元组；不处理 `knowledge_space`（那是相邻的 `_implicit_department_space_member_level`，两个分支语义**有意不同**，勿合并） |
| `permission/domain/services/permission_service.py::_resolve_org_domain_path` / `_resolve_org_domain_path_uncached` | 取映射 → 调纯函数；映射不可用时**逐级回溯兜底**（决策 6 ③），带 256 步环上界与**归档节点等同缺席**的 parity 检查 | 兜底路径只求「正确」，不求快；**必须与缓存路径给同一答案**（见 §5 坑 15） |
| `permission/domain/services/department_org_domain.py`（新） | ① `nearest_org_path` 纯函数；② 按租户的映射缓存（键 = `tenant_id`，TTL 10s，超 `MAX_CACHEABLE_DEPARTMENTS` 拒缓存）；③ `invalidate_department_mapping`；④ `build_department_mapping`（纯） | **不查库以外的 IO**；不做权限判定的最终判决；`nearest_org_path` **不查库、不缓存、不做租户判断**（纯输入输出，故可被 `permission` 域自由调用而不违反 C1） |
| `department/domain/services/department_service.py` | ① 3 处树节点字段透传（`_dept_node_dict` / `aget_tree` 内建 / pydantic 模型）；② `aupdate_department` 的标记字段级权限分支；③ `_ensure_may_change_node_type` 权限门；④ `_compute_node_type_impact` 影响面；⑤ `_aget_owned_library_ids`；⑥ `_audit_node_type_change`；⑦ `_invalidate_org_domain_mapping`（**映射 + 判定结果两个缓存**，7 个写入口调用） | 不做可见性判定（那是 `permission` 域）；影响面统计只读，不做权限授予 |
| `department/domain/constants.py`（新） | `DepartmentAuditAction` 枚举（`dept.node_type_changed` / `dept.node_moved`）——`audit_log.action` 是自由文本列，枚举是防「写进去但审计查不到」的唯一手段 | 不做任何业务逻辑 |
| `department/api/endpoints/department.py` | 新增只读端点 `GET /{dept_id}/node-type-impact`（经 `aget_node_type_impact` 做权限门） | 端点不直接 import database/models（C1 RULE-3） |
| `database/models/department.py` | 新增标记列；**不新增任何 DAO 方法** | 映射数据源 = 既有 `DepartmentDao.aget_active_by_tenant(tenant_id)`（`:501`）。C1 明文禁止为 new feature 新增 DAO 入口；不在 DAO 里做可见性判定 |

**⚠ 实现期的一次落点迁移（与 tasks 拆解不一致）**：tasks T-16/T-17 把影响面统计与审计编排写在 `knowledge/domain/services/knowledge_department_service.py`，**实际落在了 `department/domain/services/department_service.py`**。原因：影响面要读的是**部门树**（映射 + 翻转后的假想树），审计的目标也是 `department`——放在 `knowledge` 域会让该模块反向依赖部门树的内部结构。副作用：`department` 域现在直接读 `knowledge` 模型（`_aget_owned_library_ids`），这是**已知的跨域读取**，已在该函数的 docstring 里说明。`knowledge_department_service.py` 本次 **0 改动**（决策 13 也因此更容易兑现）。

**前端（platform）——§4.3 不可省的后半**（spec 有 AC-05 / AC-08 / AC-08a 三条 UI 要求）：

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `pages/DepartmentPage/components/DepartmentTree.tsx` | 树节点渲染「部门」徽标（AC-08a）。**只标部门、不标机构**——机构是默认态，标它等于给每行加零信息噪音，其缺席本身就表示「机构」。**只在组织管理页的树**，选择器不显示（组织层级概念对授权选择无关，且会改动既有行高） | 不自行判定可见范围 |
| `pages/DepartmentPage/components/DepartmentSettings.tsx` | ① 机构/部门单选（含方向性提示文案）；② **保存前的确认弹窗**：先取影响面再决定是否提交（AC-05）；③ `canEditNodeType` 的**前端镜像** | 不自行计算影响面（口径唯一在后端）；`canEditNodeType` 只是 UI 便利，后端才是权威 |
| `pages/DepartmentPage/components/NodeTypeGuideBanner.tsx`（新） | 空状态引导条（AC-08）：全树无「部门」时提示标注，可按用户 dismiss。判断基于**已加载节点**（controller 无全量遍历入口），假阴性方向安全 | 不代替实施动作——本特性上线后**不会自动生效**（决策 2） |
| `controllers/API/department.ts` | `getNodeTypeImpactApi`；标记相关请求 | 遵守既有 API Layer Pattern；**store 不得直连 HTTP**（C7） |
| `types/api/department.ts` | `NodeTypeImpact`；`is_department` 加到 `DepartmentTreeNode` / `DepartmentDetail` / `DepartmentUpdateForm` | — |

> **定稿（2026-09-21，实现期）**：落点 = **`bisheng/permission/domain/services/department_org_domain.py`**（新模块，缓存与纯函数同置）。
>
> 上面列的候选 A 约束「`permission/domain/services/` **不得** import DAO」**经查证不成立**——arch-guard 没有这条规则：RULE-4 只约束 `domain.models → domain.services`，RULE-1 只约束 `common/`、`core/`，而 `database/models/*` 是**共享层**。本仓已有同类先例：`permission/domain/services/application_permission_service.py:102` 直接 import 并调用 `DepartmentDao.aget_by_ids`。故选落点 A，无新跨模块依赖，其余两个候选不必考虑。
>
> 映射的载入用**既有的** `DepartmentDao.aget_active_by_tenant`（`:501`），不新增 DAO 入口（C1）。
>
> **缓存结构（与决策 6 的文字有别，以代码为准）**：实际存的是 `{tenant_id: {dept_id: (parent_id, path, is_department)}}`——**整行元组**，不是算好的 `nearest_org_path`。原因：影响面统计（决策 10）要对「假想翻转后的树」求值，只有持有原始字段才能在内存里构造那棵假想树；若缓存的是计算结果，就没法模拟翻转。

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | **全部标「机构」时新规则恒等于 F057**，不是近似 | 会以为「默认值随便选」，从而选到「部门」或「按层级猜」——两者都在升级瞬间全量开放历史数据且不可回滚 | 决策 2；spec AC-02 / AC-20 |
| 2 | **`nearestOrg(D)` 对 D 本身是机构时取 D 自身**，这正是「嵌套机构剪枝」的实现 | 会写成「取 D 的最近机构**祖先**」（严格祖先），于是二级分行自己会被算进父机构域 —— 等于决策 5 选了 A | 决策 3 / 决策 5；spec AC-12 |
| 3 | **`department.path` 最后一段是节点自身 id**，但 `department_service.py:1568` 的注释说「通常**不含**本部门 id」，与同文件 `:248-249` 的「the last segment is the node's OWN id」**直接矛盾** | 按 `:1568` 写会整体错位一格，祖先判定全错。（F057 design 引的是 `:1512`，该引用已随文件漂移；真实位置是 `:1568`。**注释确实是错的，只是行号会漂**——定位靠这段文字而非行号） | 以 `_parse_path_ids`（`:248`）的实现为准；陈旧注释两版都未清理 |
| 4 | **尾斜杠是前缀比较的载荷**：`/1/2/` 必须不能匹配 `/1/20/` | 去掉尾斜杠会让 2 号部门的库对 20 号部门可见（跨部门越权） | 沿用 F057 的既有比较；`test_f056_department_ownership_implicit.py:206` 钉住 |
| 5 | **部门筛选（subtree）与机构域（本特性）是两个方向不同的概念** | 会把 `department_stats` 判成「必改项」——设计初稿正是这样错的。列表收窄按 subtree，可见性判定按机构域，二者正交 | 决策 13；F057 决策 4 |
| 6 | **`is_org` 的翻转路径是一条自下而上的提权通道** | 若复用部门编辑权限，一级分行的部门管理员可解除全局屏障 | 决策 11；spec AC-04 |
| 7 | **`_scan_visible_knowledge` 是「扫到够为止」**，权限过滤越多、扫的批次越多 | 候选集放大后（决策 7 用超集），若可见率低，凑够一页要多扫几批，首屏变慢。规模已验证可接受，但**上线后要实测** | `knowledge_service.py:230`；`_KNOWLEDGE_PERMISSION_SCAN_BATCH_SIZE = 50` |
| 8 | **管理员分支与普通用户分支是两套 DAO** | 只改一条路径会导致另一半静默失效（F057 曾因此让管理员的部门筛选纹丝不动、无报错） | 参见 F057 坑 #6；本特性主要落在权限判定层（两条路径共用），但 `department_stats` 与列表的差异需在回归中覆盖 |
| 9 | **`PermissionCache.invalidate_all()` 的 SCAN pattern 不带租户前缀** | 多租户下会跨租户清「判定结果」缓存（后果轻微：清了只是重算）。**注意**：这与决策 6 ① 禁止的「跨租户共享**路径映射**」严重性不同级——后者会算错，前者只会重算 | 决策 10 的「已知副作用」 |
| 9b | **决策 6 的缓存键必须含租户，否则是跨租户越权**（C3） | 部门 id 跨租户可能重复，A 租户的判定会命中 B 租户的机构路径 | 决策 6 ①；实现与测试都必须覆盖 |
| 10 | **F057 明确拒绝过「缓存部门链」**（其理由：`path` 在 reparent 时重写，缓存会静默过期） | 会直接引用该结论否决决策 6。但 F057 拒绝的是**请求内缓存一次就算数**的语义，本特性是**进程级 + 显式失效**，失效语义不同 | 决策 6 的「代价与必须的处理」 |
| 11 | **本特性上线后不会自动生效** | 会被当作「功能没做」而报障；实际是决策 2 的安全缺省使然，需实施先标注组织树 | 决策 2 的「代价」；spec §已知缺口 |
| 12 | **平台请求拦截器已解包 `response.data.data`** | 再写 `res?.data` 会恒为 `undefined`，链路静默失效 | F057 坑 #3；新增前端代码同样适用 |
| 13 | **本仓已知存在 3w+ 部门的部署**（`department_service.py:1518`），比目标客户大一个数量级，且已有专门绕开全量加载的优化 | 会把「整表载入」当成放之四海皆准的做法，在超大租户上重蹈 `:1519` 记录的达梦 bind param 序列化瓶颈 | 决策 6 ③ 的退化路径；决策 7 的规模边界。**两条决策的阈值必须一致** |
| 14 | **C1 明文禁止为 new feature 新增 DAO 入口** | 会顺手写一个 `aget_all_for_permission_map()`。本特性**不需要**——`DepartmentDao.aget_active_by_tenant`（`department.py:501`）已经就是那个查询 | 决策 6；§4.3 的 DAO 行 |
| 15 | **同一个规则的两条路径必须给同一答案，且分歧不能偏「放宽」**：映射走 `aget_active_by_tenant`（**过滤** `status='active'`），兜底走 `aget_by_id`（**不过滤**） | 归档的机构祖先在映射里**缺席**→缓存路径判「拒」；而兜底路径能读到它、继续走、返回其 path→判「放行」。于是**同一个租户，走兜底时可见性更宽**（超过 5000 节点或映射加载失败时自动发生）。两条路径的差异必须是显式检查出来的，不能靠「反正数据一样」 | `PermissionService._resolve_org_domain_path_uncached` 里的 `status != "active" → None`；`test_f062_department_mapping_cache.py::test_uncached_fallback_agrees_with_the_cached_path_on_archived_nodes` 钉住 |
| 16 | **`_compute_node_type_impact` 的观察者必须是「全部部门」，不能是「子树内」** | 翻转载记改变的是**子树外的人能不能看到子树内的库**——这恰恰是「解除屏障」的含义。把观察者限定在子树内，会对唯一真正会变的人群视而不见，**恒返回 0**；而弹窗显示 0 会让操作者以为此操作无害，比没有弹窗更糟 | `_compute_node_type_impact` 的 `all_viewers = list(mapping.keys())`；tests T-15 |
| 17 | **`PermissionCache`（判定结果）与 `_cache`（映射）是两个缓存，清一个不等于清另一个** | 只失效映射，`PermissionService.check` 已返回的「允许」还会活满 10 秒 TTL——翻转**收回**权限时这就是一个 stale-allow 窗口，正是决策 10 要消除的方向。两个都要清 | `_invalidate_org_domain_mapping` 同时调 `invalidate_department_mapping()` 与 `PermissionCache.invalidate_all()` |
| 18 | **`department` 表的写路径上，「归档只读」守卫是每个入口各一份的** | 加新字段时很容易在重写某个方法体时**连带删掉**它（实现期确实在 `aupdate_department` 发生过一次）。后果是归档部门可被改名/改组/翻转标记——而 F057 已把「归档即失效」定为口径，破坏它就等于让「已删除」的部门继续授权 | 8 处 `if dept.status == "archived": raise DepartmentArchivedReadonlyError()`；`test_f062_node_type_permission.py::test_archived_department_is_still_readonly` 钉住 |
| 19 | **平台用户载荷里没有 `is_tenant_admin`，租户管理员是 `is_child_admin`** | 前端按 `is_tenant_admin` 判权限会恒为 `undefined`：**租户管理员拿到禁用控件**（后端本会放行），**部门管理员拿到可用控件**（后端必 403）。写前端权限镜像前先 grep 真实字段名 | `DepartmentSettings.tsx::canEditNodeType`；既有用法见 `MainLayout` / `HeaderMenu` / `ModelPage/manage/permissions.ts` |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `department` 的机构/部门标记列 | DB 列（非空、`server_default 0`，无外键） | 本特性；未来任何按机构域盘点的功能 |
| `DepartmentTreeNode` 新增标记字段 | HTTP API 响应字段 | platform 组织管理页、部门选择器、`useLazyDepartmentTree` |
| 组织树知识库可见范围规则 | 内部行为契约 | 客户端组织知识库列表、平台知识库列表、部门角标、工作台对话检索、灵思任务内核白名单（**全部自动跟随**，见 INV-21） |
| `GET /api/v1/departments/{dept_id}/node-type-impact` | HTTP API（只读；与翻转同一个权限门） | platform 组织管理页的确认弹窗（T-21） |
| `audit_log` 的 `dept.node_type_changed` / `dept.node_moved` 行 | 审计记录（`target_type=department`、`target_id=dept_id`、`metadata={from_is_department, to_is_department, affected_departments, affected_libraries}`，移动另加 `{from_parent_id, to_parent_id}`） | 合规取证；**注意**：这两个 action **未加入** `_UI_VISIBLE_V2_ACTIONS`（`audit_log.py:178`），因此不在「系统操作」页展示，但**照常落库**。tasks T-17 曾要求加入白名单，实现时按该元组的既有约定（dept.* 属隐藏项）未加——如需在 UI 展示，改该元组并同步前端 `getActionsApi` |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `department.path` 的物化路径语义与维护（含尾斜杠、末段为自身 id） | 隐式数据契约 | 若未来改为邻接表或 path 语义变更，判定式全部失效 |
| `UserDepartmentDao.aget_user_departments` | DAO | 用户部门关系是可见性的输入；多部门并集语义依赖它返回**全部**归属（不区分主/兼职） |
| `PermissionService` 的五级短路与隐式回退位 | 内部 API | 判定挂在「FGA 判否后的隐式回退」上；若短路口径调整，生效点随之改变 |
| F057 的 `knowledge.department_id` 归属列与归属写入链路 | DB 列 + Service | 本特性只读该列；若归属语义变化（如改为多归属）判定式需重建 |
| `PermissionCache` 的 key 结构与 TTL | 内部 API | 决策 10 依赖 `invalidate_all()` 的既有实现与 10 秒 TTL |
| `DepartmentArchiveCleanupService` 的调用时机 | 内部 API | spec AC-20a / AC-20b 要求机构归档后**收紧**可见性，校验点在该服务的调用链上 |

---

## 7. 测试与可观测

**测试策略**

- **规则真值表（最高优先级）**：必须按 spec §2.2 的判定式建一张**树形 × 归属位置 × 用户位置**的矩阵用例。最小覆盖：用户在你给的 6 节点例子上恰好可见 6 个节点（spec AC-01~AC-20 的合成场景）、全标机构 = F057 逐条相同、嵌套机构（含节点自身与域内两种）不可见、部门套多层可穿透、多部门并集、无部门为空、根节点类型无差异。
- **升级不变性**：AC-02 是安全基线，必须有独立用例——构造一批存量数据，跑升级前后的同一组可见性断言，要求**逐条相同**。这条不能只靠推理。
- **跨入口一致性**：客户端列表 / 平台列表 / 部门角标 / 检索白名单四处取同一批断言，防止某条路径漏接。
- **性能**：`_scan_visible_knowledge` 在超集候选下的补页批次数（坑 #7）需实测；部门表映射缓存命中率与陈旧窗口需有日志。

**必须有独立用例的三条（都是「不写就会静默错」的类型）**

1. **缓存按租户隔离**（决策 6 ①）：构造两个租户、**相同的部门 id 但不同的树形**，断言租户 A 的判定不受租户 B 的映射影响。这是 C3 门禁项，变异测试应能把「去掉租户键」这一改动测挂。
2. **大租户退化路径**（决策 6 ③）：构造超过阈值的节点数，断言走的是逐级回溯分支、**没有**触发全量载入。
3. **机构归档 fail-closed 方向**（spec AC-20a）：归档一个机构，断言子树用户**失去**可见性，且**没有**因此看到归档前不可见的知识库。方向写反会变成越权，而正向用例测不出来。

**手动验证（可直接照抄）**

前置：平台端 `:3001`、后端 `:7860`、客户端 `:4001`。准备三个账号——**超管**（改标记）、**U1**（挂在「信贷审批部2」）、**U2**（挂在「信贷审批部1」）。在平台组织管理页建出这棵树：

```
总行/省联社 (0-机构, 默认)
├── 信贷研发      (标为 1-部门)
├── 信贷审批部    (标为 1-部门)
└── 一级分行      (0-机构, 默认)
    ├── 信贷审批部1 (1-部门)
    └── 信贷审批部2 (1-部门)
```

给上述 6 个节点各挂 1 个组织知识库（`KnowledgeDepartmentService` 的指派入口）。然后：

1. **核心可见范围**：U1 登录客户端 → 进入对话 → 打开知识库选择器 → 组织知识库分组应恰好出现 **6 个库**。U1 与 U2 的结果应**完全相同**（同处一级分行机构域）。
2. **嵌套机构边界**：在 `一级分行` 下再建 `二级支行`（保持 `0-机构`）并挂一个库 → U1 刷新后**不应看到**该库。
3. **翻转保护**：超管把 `一级分行` 翻成 `1-部门` → 应弹确认框并显示影响面数字（部门数 / 知识库数）→ 确认后，`二级支行` 的库对 U1 **变为可见**（因一级分行不再构成屏障）。再翻回 `0-机构`，应恢复不可见。
4. **缓存即时性**（决策 10）：翻转后 **10 秒内**用 U2 在客户端刷新列表，已收回的库**不应**出现。
5. **权限级别**（spec AC-17）：U1 在客户端对横流来的库（如 `信贷研发` 的库）应可选中并用于问答，但**没有**编辑 / 删除入口。
6. **归档 fail-closed**（spec AC-20a）：归档 `一级分行` → U1 应**失去**该机构域派生的横向可见性，且**没有**因此看到归档前不可见的内容。
7. **角标一致性**（spec AC-22）：平台知识库管理页左侧树上 `总行` 的角标数字，应等于点击该节点后列表实际返回的条数。
8. **升级不变性**（spec AC-02，最高优先级）：把全部节点改回 `0-机构` → 可见范围应与**升级前**逐条相同（此时横向互通完全消失，只剩 F057 的纵向传递）。

**可观测**

- 判定路径需有 `logger.info` 级别的影响面摘要（沿用 `_scan_visible_knowledge` 与 `filter_knowledge_ids_by_permission_async` 既有的 `[perf][knowledge.*]` 日志形态），便于对比升级前后的扫描批次与耗时。
- 标记翻转 / 移动的审计留痕（决策 10）是排障时的第一手材料——可直接查 `audit_log` 表中 `target_type='department'` 的行。

---

## 8. 后续改进 / 不打算做的事

- **不做的**：deny / 排除机制（决策 12）、可见集合物化列（决策 7）、知识空间穿透（决策 9）、标记继承（spec 明确排除）。
- **已知短板**：
  - 隐含授权不可撤销（INV-20），隔离只能靠改树结构，影响面可能超出操作者预期。
  - 决策 6 的缓存有陈旧窗口（TTL 量级）。
  - 决策 10 的 `invalidate_all` 跨租户清缓存（后果轻微）。
  - **缓存无容量上界**（决策 6 ② 只兑现了一半）：不再被访问的租户条目永久驻留，内存随「租户数 × 树规模」单调增长。**触发条件**：租户数上百后需加 LRU 或全局条目上限。
  - **超大租户的退化路径没有请求内 memo**（决策 6 ③ 只兑现了一半）：功能正确，但该规模下的性能等于被否决的方案 A。**触发条件**：真的有 5000+ 节点的租户接入时。
  - **AC-23 / AC-24 检索期一致性未实现**：linsight 的检索白名单
    （`task_exec._resolve_allowed_knowledge_ids`）从**会话存的 id** 构建，
    `SearchKnowledgeBase` 只校验「id 在不在白名单里」（`linsight_knowledge.py:57`），
    **检索时不重算可见性**；而会话 id 是客户端提交后原样落库的
    （`workbench_impl.py:267`）。所以一个会话可以继续搜一个已不可见的库。
    **这早于 F062**（是会话模型「你选了什么就搜什么」的固有语义），修它需要每轮
    检索前重做可见性校验，是 **linsight 侧的独立改动**，有自己的 AC。INV-21 声称
    标记对所有读取入口生效——这里是它目前不成立的地方，**记录而非掩盖**。
  - **移动影响面不校验目标父节点的合法性**：`_compute_move_impact` 对
    「不存在 / 跨租户 / 是自己或自己的后代」的 `new_parent_id` 会产出无意义的 0
    或一棵不可能存在的树（如 `/1/2/3/2/3/`），而不是报错。`amove_department`
    本身会用环检测拒绝，所以**不会写坏数据**，但弹窗数字可能无意义。
  - **批量影响面「逐节点求和」会低估**：选择集内部有交互（祖先与后代同选时，
    同时翻转它们的真实影响 ≠ 各自影响之和）。当前实现是逐节点预览后求和。
  - **`dept_ids` 未去重**：`["A","A"]` 会让 A 进 plans 两次，产生两条相同审计行、
    计数虚高。前端用 Set 不会触发，仅直接调接口可达。
  - **`_compute_node_type_impact` 的调用位置**：`_audit_node_type_change` 在
    commit + 缓存失效**之后**调用它，此时映射已是**翻转后**的新树，因此函数内的
    `mapping` / `flipped` 命名与实际语义相反（对称差使计数仍正确）。
    **触发条件**：改动该函数前必须先读这一段。`_compute_move_impact` 没有这个问题
    ——它在 commit **前**计算（移动不可逆，事后算不出「之前」）。
- **重写触发条件**：
  - 组织节点数增长到万级 → 重新评估决策 6 与决策 7。
  - 「单库敏感」成为高频刚性诉求 → 回到决策 12 的 B 方案。
  - 部门树变更频率显著上升（SSO 高频同步）→ 决策 6 改为同步完成钩子失效。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-21 | 初版 | grilling 定稿后编写；含对设计初稿「`department_stats` 必改」错误判断的纠正（决策 13） |
| 2026-09-21 | 首轮 design 评审修复 | ① **C3 BLOCKER**：决策 6 补「缓存键必须含租户」门禁项；② **规模前提**：补「本仓已知 3w+ 部门部署」的约束与决策 6 ③ 大租户退化路径（阈值 5000），决策 7 与之一致；③ **勘误**：决策 6 的「59 处调用方」实测为 9/1/4，否决 B 的理由重写；④ §4.3 补前端模块行与缓存落点的 C1 约束；⑤ 决策 10 补审计落点（复用 `audit_log` + `ainsert_v2`）；⑥ §5 补坑 9b/13/14，修正坑 3 的行号漂移；⑦ §7 补三条必测用例与可照抄的手动验证步骤 |
| 2026-09-21 | Wave B/C/D 实现期 + code-review 修复 | ① **落点迁移**：影响面统计与审计从 `knowledge_department_service.py` 改到 `department_service.py`（§4.3 已说明，含跨域读 `knowledge` 模型的已知副作用）；② **§4.2 补新端点契约** `GET /departments/{dept_id}/node-type-impact` 与 `is_department` 在四处的字段约定；③ **§4.3** 去掉重复行、收录 5 个实际文件（`department_org_domain.py` / `constants.py` / 前端 3 个组件）、缓存结构改为「整行元组」；④ **§6.1** 登记端点与审计行，并写明两个 action **未加入** `_UI_VISIBLE_V2_ACTIONS`；⑤ **§5 新增坑 15~19**（两条路径的归档答案必须一致 / 影响面观察者不能限子树 / 两个缓存要一起清 / 归档守卫逐入口各一份 / `is_tenant_child` 不是 `is_tenant_admin`）；⑥ **决策 6** 更正 ① 的租户理由（id 全局唯一，不是会撞）、标注 ②③ 各自只兑现一半；⑦ **决策 10** 补 `invalidate_all` 是两个缓存之一；⑧ **§8** 补 7 条已知短板（其中「批量标注 UI 未实现」「移动无确认弹窗」与 tasks 的 ✅ 不符） | code-review 两轮（2 agent × 6 维度）+ 实现期实测 |
| 2026-09-21 | Wave 0 / A 实现期同步 | ① **§4.3 落点定稿**：定为 `permission/domain/services/department_org_domain.py`（新模块）。**推翻候选 A 的前提**——「permission 域不得 import DAO」在 arch-guard 中并不存在（RULE-4 只约束 `domain.models → domain.services`），且 `application_permission_service.py:102` 已有直接 import `DepartmentDao` 的先例；② **§4.3 新增模块行**：`department_org_domain`（按租户映射缓存 + 纯函数）、`database/models/department.py`（标注「不新增 DAO 入口」）；③ **决策 6 ③ 的具体化**：`_resolve_org_domain_path_uncached`（`permission_service.py`，逐级回溯 + 256 步环上界）为 mapping 不可用时的兜底，非新决策；④ **§5 新增坑 14**：C1 禁止为 new feature 新增 DAO 入口——映射数据源用既有的 `DepartmentDao.aget_active_by_tenant`；⑤ **T-09 失效点实测为 7 处**（6 个写入口 + `acreate_root_department`），非 tasks 泛指的「新增/改名/移动/归档/翻转」四类；⑥ **T-03 树节点构造点实测为 3 处**、**T-04 手写 DDL 副本实测为 10 个文件**，均已回写 tasks「实际偏差记录」 |
