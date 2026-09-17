# Code Review Report — F058 文档抽取模板

**Feature**: 058-doc-extract-template
**Review scope**: `ed68ccfdf..HEAD` — F058 的全部提交（15 个），**不含**该区间之外的并行工作流改动
**Base**: `feat/2.7.0/045-minimax-tts-adapter`（注：与 `feat/2.7.0/guangxi-ns-llm-guardrail` 的完整分歧有 534 文件，其中 F058 仅占 67 个）
**Changed files**: 67（8433 insertions / 9 deletions）
**Review date**: 2026-09-17

---

## Summary

| Dimension | High | Medium | Low | Status |
|-----------|------|--------|-----|--------|
| Boundary Conditions | 1 | 5 | 6 | **NEEDS_FIX** |
| Permission & Auth | 1 | 2 | 0 | **NEEDS_FIX** |
| Concurrency Safety | 0 | 1 | 1 | PASS_WITH_WARNINGS |
| Information Leakage | 0 | 1 | 1 | PASS_WITH_WARNINGS |
| Test Coverage | — | — | — | （见 §测试覆盖） |
| Code Style | — | — | — | （见 §代码风格） |
| Docs Sync | 0 | 0 | 0 | PASS（实现期已同步，见 §文档同步） |

## Overall: **NEEDS_FIX**

两条 HIGH 都经过本人**实际执行验证**（非静态推断），且第 2 条是真实可触发的**跨租户越权写入**。

---

## HIGH

### H1. 模型槽位值解析错误 —— 功能整体不可用

**位置**：`src/backend/bisheng/doc_extract/domain/services/extraction_service.py:113`

`TenantSystemModelConfigDao.resolve()` 返回的是数据库 `value` 列的**原始字符串**（`LargeText`）；平台所有既有消费方都走 `json.loads(value)`（见 `llm/domain/services/llm.py:291`：`model_cls(**(json.loads(value) if value else {}))`）。而本实现写的是：

```python
if isinstance(value, dict):          # 永不可达 —— value 是 str
    ...
return int(value)                    # int('{"model_id": 5}') → ValueError → None
```

**实测**（本人执行）：

| 存储内容 | 解析结果 |
|---|---|
| `{"model_id": 5}`（真实存储形态） | `None` |
| `{"model_id": 3, "temperature": 0}` | `None` |
| `3`（裸数字，非本平台格式） | `3` |

**失败场景**：运维按 `tasks.md` 明示的 `{"model_id": N}` 写入该槽位 → 解析恒为 None → 每次抽取在 precheck 第 3 行抛 **11608「当前租户未配置文档抽取模型」** → **抽取功能整体不可用**，且错误信息指向"去配模型"，把排障引向错误方向。

**为何单测没抓到**：`test_extraction_service.py` 全程 patch `_resolve_model_id` 直接返回整数，绕过了这段解析。e2e 的 3 条抽取用例又因槽位未配置而 skip。**这段代码从未被真正执行过。**

**修复**：`json.loads` 后再取 `model_id`（参照 `llm.py:291`），并对解析失败保持 fail-fast 可观测。

---

### H2. 跨租户模板写入 —— 子租户管理员可改 / 删 Root 的模板

**位置**：`domain/models/extract_template.py:141`（`aupdate`）、`:155`（`asoft_delete`）、`:96`（`aget_by_id` 的 SELECT）
**调用方**：`template_service.py:140-160`、`api/endpoints/templates.py:74-120`

**根因链**（逐环节实测确认）：

1. 子租户的全局租户过滤器是 **IN-list**：`WHERE tenant_id IN (child, 1)`（`utils/http_middleware.py:216-218`，`_compute_visible_tenant_ids`）→ **SELECT 能命中 Root 的模板行**。
2. `do_orm_execute` 监听器**只处理 SELECT**：`tenant_filter.py:169-170` 是 `if not orm_execute_state.is_select: return` → 随后的**脏刷 UPDATE / DELETE 完全不被过滤**。
3. `before_flush` 只填 `session.new`（新建行），**不管已加载行的修改**。
4. `TemplateService.aupdate / aset_enabled / adelete` **只判存在性，从不校验 `template.tenant_id == 当前租户`**（全模块 grep 无任何租户比对）。

**失败场景**：租户 2 的 Child Admin 发 `PUT /api/v1/doc_extract/templates/1`（id 可枚举）→ `aget_by_id(1)` 因 IN-list 命中 **Root 租户的模板** → 服务层放行 → UPDATE 无租户过滤 → **Root 该文档类型的 `field_schema` / `render_template` 被改写成攻击者的定义**。此后 Root 侧所有业务抽取按被篡改的 schema 输出（字段被替换或丢失）。`DELETE` 同理（软删后 Root 业务调用方持续拿 11601/11606）。

**违反**：spec **AC-05**（"不属于当前租户的模板须拒绝并返回 11611"）。

**这不是理论风险** —— 平台自己的代码**明确记录过这个坑并给了修法**：

- `linsight/domain/models/linsight_skill.py:95-104` 的类文档字符串逐字描述了这个陷阱：
  > "The global tenant filter defaults to the F012 IN-list `WHERE tenant_id IN (leaf, ROOT)` for child tenants, which would otherwise leak Root skills into a child's list. So every SELECT here runs inside `strict_tenant_filter()` … Bulk `UPDATE` statements are *not* rewritten by the do_orm_execute listener — it only touches SELECTs — so they carry an explicit `WHERE tenant_id = current` of their own."
- `knowledge_department_service.py:303` 记录了同一坑。
- F059 同类模块也做了收窄：`sensitive_word/domain/services/chat_guard.py:124`、`sensitive_word_policy_service.py:56-58` 均显式取 `get_current_tenant_id() or login_user.tenant_id`。

F058 的 DAO 三处**都没有做**。

**修复**：SELECT 包 `strict_tenant_filter()`（收窄 IN-list 到 `tenant_id = current`），并在写路径显式校验归属。**注意**：`doc_extract_template` 是租户级配置、**不应**像 `knowledge_space` 那样允许读 Root 共享行 —— 本特性的设计前提是"模板属于当前租户"。

---

## MEDIUM

### M1. 判定候选集与模板读取未限定租户（H2 同根因）
`classifier_service.py:112`（`alist_templates(enabled_only=True)`）、`extraction_service.py:258`、`extract_run.py:101`

Root 的启用模板会进入**子租户**的判定候选集并被直接使用；`_record_success` 还用模板的 `tenant_id` 落库（`extraction_service.py:430`），导致子租户文档的抽取记录被写进 **Root 租户**。
**失败场景**：租户 2 的普通登录用户调 `/classify` → 响应回显 Root 的 `doc_type` + `template_id`；再调 `/extract` → 按 Root 的字段定义抽取并返回。**违反 AC-17 / AC-35**。

### M2. `tenant_id` 从未传入模型槽位解析
`extraction_service.py:232`；调用方 `api/endpoints/extraction.py:82`（显式传 `None`）、`:148-153`

`resolve(None, 'doc_extract_llm')` 的 `get(None, ...)` 生成 `tenant_id IS NULL`，与该 NOT NULL 列永不相交 → **子租户自己的配置永远读不到**，一律落到 Root 回退分支。
**失败场景**：租户 2 管理员在租户 2 配好了模型，抽取仍跑在 Root 的模型上（计费/能力被 Root 控制）；若 Root 未开 `share_default_to_children`，则恒定收 11608「未配置」——**明明配了却说没配**。另：`ClassifierService.aclassify` 的 `tenant_id` 形参在函数体内从未使用。

### M3. 唯一性校验发生在归一化之前
`template_service.py:103`（查重用未 strip 的 `doc_type`）vs `:107`（落库用 `doc_type.strip()`）

**失败场景**：已有 `business_license` 时 POST `'  business_license'`（前导空格；MySQL `utf8mb4_unicode_ci` 是 PAD SPACE，只忽略**尾随**空格）→ 查重未命中 → 落库被 strip 成同标识 → **同租户两条 live 同标识模板**（AC-07 失效）。此后 `aget_by_doc_type(...).first()` 无 ORDER BY，随机命中；分类候选字典 `{doc_type: t}` 静默丢一条。`aupdate` 改标识同理。

### M4. 分页 `limit` 无边界校验
`domain/models/extract_run.py:121`；端点 `api/endpoints/records.py:46` 是裸 `limit: int = 20`

`rows[:limit]` 后取 `rows[-1]`，当 `limit <= 0` 时切片为空 → `IndexError` → 500。
**失败场景**：`GET /records?limit=0` 且已有记录 → 500；`limit=-1` → 返回 4 行且 `next_cursor` 错乱；`limit=1000000` 无上限，可一次拉全表。
**修法**：按平台惯例加 `Query(20, ge=1, le=N)`（参照 `channel_manager.py:147`）。

### M5. 120 秒超时是设计承诺但未实现
`extraction_service.py:364`（design §2 / 决策 14 承诺"同步等待上限 120 秒，超出即按 11608 返回"）

全模块无 `timeout` / `wait_for`；解析侧沿用的 MinerU loader 默认 `timeout=600`（`knowledge/rag/pipeline/loader/mineru.py:108,208`），分类与抽取还是两次串行模型调用。
**失败场景**：识别服务卡住 → 单请求最长挂 600s+，**网关/前端先超时（504）**，调用方拿到的是网关错误而非设计承诺的 11608，期间还占着一个 worker。

### M6. 识别服务调用失败被误报为"未配置"
`parse_adapter.py:121`

`:99-106` 的 `except Exception` 把解析异常吞掉，使"识别服务已配置但调用失败/超时"（spec 行 9 → 11608）与"没有识别服务"（行 4 → 11605）**坍缩成同一个 11605**；且该抛点在 `extraction_service.py:266`，位于失败记录 try（`:289`）之外 → **M-1a 下不落失败记录，违反 AC-03**。
**失败场景**：MinerU 报错 → 前端提示"未配置识别服务"，运维按 `no_parser` 去查配置，**排障方向被指错**。

---

## Information Leakage

### L1（MEDIUM）. 错误信息回显模型原始输出，落进记录与日志
`extraction_service.py:143`（同类 `classifier_service.py:74`）；落库 `:458`

`msg=f'模型输出不是合法 JSON：{text[:200]}'` 有**三条外泄路径**：① 作为 `status_message` 返回调用方；② 写入 `doc_extract_run.error_message`（VARCHAR(500)）；③ 由全局异常处理器 `main.py:34` 打进应用日志。
**失败场景**：一张**身份证**图片抽取时模型首轮回复被截断 → 前 200 字符（含姓名/证件号）被写进服务端日志并持久化；管理员在抽取记录页展开详情（`DocExtractRecords.tsx:140-144` 直接 `JSON.stringify` 渲染）即可看到原文。design §4.2 只授权该列承载"失败原因"，**未授权承载文档内容**。

### L2（LOW）. 用户可见错误内联底层异常原文
`extraction_service.py:356`、`:368`、`classifier_service.py:137`

`f'抽取模型调用失败：{exc}'` —— openai/httpx 系异常的字符串通常含请求 URL 与网关返回体。
**失败场景**：模型不可达时，任意登录用户的响应体（及记录详情）出现 `ConnectError ... http://<内网IP>:<端口>/...`，**披露内网拓扑**。

---

## Boundary Conditions（其余 LOW）

| # | 位置 | 问题 | 场景 |
|---|---|---|---|
| B1 | `domain/schemas.py:18` | `name`/`doc_type` 无 `max_length`（平台惯例见 `tenant_schema.py:17,126`），服务层也不校验 | 200 字符的 `name` → MySQL 严格模式 500，非严格模式静默截断致同名 |
| B2 | `template_service.py:57` | `(field.get('key') or '').strip()` 假定 key 是字符串 | `{"key":123}` → AttributeError → 500（而非 11603） |
| B3 | `api/endpoints/extraction.py:123` | `json.loads(trial_definition)` 在 try 之外，且未校验结果类型 | `-F 'trial_definition={oops'` → JSONDecodeError → 500（而非 AC-42 的 11603/11604） |
| B4 | `api/endpoints/extraction.py:38` | 上传文件名未校验即参与落盘路径；`_stage_upload` 抛错时 `_cleanup` 不被调用 | `filename=..` → PermissionError 500，且每次请求泄漏一个 `doc_extract_*` 临时目录 |
| B5 | `api/endpoints/extraction.py:82` | `/classify` 跳过行 3 预检 | 未配模型时传扫描件 → 先付一次完整解析（MinerU 最长 600s）才返回 11608 |
| B6 | `TrialExtractDialog.tsx:77` | 前端 `REASON_KEY` 与后端 `_reason_for`（`extraction.py:166-175`）的 token 不一致：后端发 `content_fetch_failed`/`type_unavailable`/`type_unknown`，前端未映射 → 一律显示"未知失败原因" | 试抽取撞上 11608/11606 → 管理员无法区分"模板配错"还是"环境没配"，**正是 AC-44 要消除的不确定** |

---

## 已验证的非缺陷（记录以免后人重复怀疑）

- **`assert_trial_allowed` 未被绕过**：未认证/无 user_id → 11611；两个分支都是"必须为真才放行"，叠加 `has_tenant_admin` 自身 fail-closed，**整体 fail-closed**。四条路径（普通用户 / Root 普通用户 / 子租户管理员 / super）逐一走过，无放行路径。唯一偏离：design 决策 13 要求"抽成共享 helper"未落实（此处为复制实现），但方向**更保守**，不构成缺口。
- **决策 8 的 C4 论证成立**：`permission_schema.py:8-19` 的 `VALID_RESOURCE_TYPES` 恰为九类且不含模板；同构前例 `sensitive_word/api/endpoints/policies.py:19,33`；`doc_extract` 全模块无任何 `PermissionService` 调用。**但前提是"模板就是当前租户的东西"—— H2/M1 正是该前提在 Root↔子租户方向不成立。** C4 结论对，模板的租户边界仍需 DAO 层自己守。
- **审计白名单三处同步有测试守护**（`test_audit_lockstep.py`），`log.ts` 镜像齐全。
- **审计写入 best-effort 符合决策 11**：`audit.py:52-72` 包 try/except，与模板写入分属两个事务，失败不反噬主流程。
- **分页溢出本身正确**：`cursor` 超范围 / 为 0 / 为负均返回空列表 + `next_cursor=null`，不报错。唯一缺陷是 `limit`（M4）。
- **空集合路径无异常**：无已启用模板 → 11606；空 `field_schema` 在保存期即拒；`render(template, None)`、空 `trial_definition` 均正常。

---

## 测试覆盖（维度 5）

**核心结论：mock 策略是本特性前两轮所有漏网 bug 的共同成因。**

| 位置 | 问题 | 严重度 |
|---|---|---|
| `test_doc_extract_api.py` | 把 `ExtractionService.extract` 与 `TemplateService` 的增删改**整体 patch 掉**，因此只验证「路由存在 + 权限 Depends + 响应外壳」，**零服务逻辑**。`test_business_failure_is_an_http_error` 是让 mock 抛异常再断言状态码，证明不了真实异常会走到那里 | medium |
| 同上 | `GET /templates/{id}`、`PUT /templates/{id}` **一条测试都没有**；AC-42/AC-43/AC-35 在该文件完全缺席；`reason` 只覆盖了 `no_parser` 一个 token | medium |
| `test_template_service.py:178` | `test_identifier_is_reusable_after_delete` 把 `aget_by_doc_type` mock 成返回 None，**只断言 `audit_spy` 被调用** —— 对 AC-08 本身无断言。真正的规则落在 DAO 的 `_live_stmt()`（`is_deleted == 0`），而它在这个文件里永远被 mock 盖住。**把该过滤删掉，这条测试依然全绿** | medium |
| `test_extraction_service.py` | **M-2b 自动判定编排路径（AC-18/AC-20）零覆盖** —— `aclassify` / `determined_by='auto'` / `doc_type=None` 在该文件 grep 零命中。分类服务本身有测试，但「分类后如何接着抽取」没有 | high |
| `extract_run.py:88-135` | 记录列表的过滤（template_id / status / 时间范围 / cursor 翻页）**在任何一层都没有测试** —— DAO 单测、API、e2e 全缺。`id < cursor` 写成 `<=` 会翻页死循环，现在没人拦得住 | medium |
| `test_extraction_service.py:105` | 死代码：`_patch_template()` 定义后从未被调用 | low |

**`tasks.md:14` 声称「46 条 AC 全覆盖」不属实** —— 至少 AC-08、AC-13、AC-18、AC-20、AC-24、AC-30、AC-35 无测试。

---

## 代码风格（维度 6）

**LGTM 的部分**（工具验证，非目测）：`npx eslint` 对 F058 全部 9 个前端文件**无输出**；无 `backdrop-filter` / `backdrop-blur`；未引入冻结依赖；F058 前端源码**无硬编码中文**；无直接 `import axios`；未触碰 `request.ts` 的解包层。后端 ruff 的真实项只有 F841×2 / I001×2 / RUF005 / RUF059 / UP042 共 7 条（其余 95 条是 RUF001 全角标点噪音，`sensitive_word` 模块同类），**无 arch-guard 违规**。

| 位置 | 问题 | 严重度 |
|---|---|---|
| `TrialExtractDialog.tsx` | `REASON_KEY` 与后端 `_reason_for()` 不一致（详见上节） | high（已修） |
| `log.ts` | 审计模块镜像只补了一半（详见上节） | medium（已修） |
| `extraction_service.py:331` / `:417` | F841：未使用的 `render_template` 绑定与 `as exc` | low（已修） |
| `extract_template.py` / `parse_adapter.py` 等 | 死代码：DAO 的三个**同步**方法全项目无调用方；`ParseAdapter.cleanup_temp_file()` 无人调用；`write_template_audit(extra=)` / `ClassifierService.aclassify(tenant_id=)` 参数未被使用；`ExtractResult` 的 `error_code/error_message/reason` 三字段从未被赋值 | low（同步方法已删） |
| `classifier_service.py` + `extraction_service.py:63` | `_JSON_FENCE_RE` 同一正则定义两份 | low |
| `extraction.py:65-108` | 层间穿透：端点直接调 `ExtractionService` 的三个私有方法（`_assert_readable` / `_assert_within_size` / `_resolve_model_id`）；docstring 仍称权限门槛不在端点里 | low |

---

## 文档同步（维度 7）

| 位置 | 问题 | 严重度 |
|---|---|---|
| `design.md:15,228` | 自认「实现尚未开始」，而代码/迁移/e2e 均已在库 —— 作为「现状快照」整体失效 | medium（已更新） |
| `design.md:332-345` | §4.2 的 `reason` 枚举表列 10 个值，实现只发 8 个；**前端 `REASON_KEY` 就是照这张旧表写的** —— 文档失真直接导致了一个 high 级缺陷 | medium（已修） |
| `design.md:362,421` | 引用不存在的 `record_query_service.py`；§6 依赖仍写旧的 `AuditLogService`/`EventType`/`ObjectType`，与已改写的决策 11（v2 审计路径）矛盾 | medium |
| `design.md:410,437` | §6.1 endpoint 清单缺 `PUT /templates/{id}/enabled`；§7 承诺「每次拒绝都记日志」，但第 1-8 行的拒绝只打日志且日志点不全 | medium |
| `design.md` 决策 14 | **120 秒超时设计写了、代码没有、偏差没记** —— `extraction_service.py` 无 `asyncio.wait_for`，模型调用可无限期挂住 | medium |
| `tasks.md:25,32,74,875` | 三处失真：T-33 标注「未提交」但实际已在 HEAD（F059 的提交带上了它）；`:74` 写「错误码模块 114」与同文件 `:88/:104` 及实现（全 11601-11611）自相矛盾；`:875` 偏差记录仍是「待填」 | medium |
| `release-contract.md` | 114→116 的最终模块号重分配未进变更历史（表体本身与实现一致） | low |

**design 与 spec 无直接矛盾**；唯一实质分叉在实现层（AC-24 不可达，已修）。另：`tasks.md:412/:429` 写「100 字下限」而 design 写 10 —— 实际是 10，tasks.md 那两处是改号前的残留。

---

## 结论

**初判 NEEDS_FIX → 两条 HIGH 已修复并验证；剩余 4 条 MEDIUM 不阻塞合入。**

### 已修复（含验证方式）

| 项 | 修复 | 验证 |
|---|---|---|
| **H1** 槽位解析（功能完全不可用） | `_parse_slot_value` 先 `json.loads` | 10 个真实存储形态用例；**修复前 `{"model_id": 5}` → None** |
| **H2** 跨租户越权写 | 读路径 `strict_tenant_filter()` + 写路径复检归属 | `test_tenant_isolation.py` 7 条；**故意摘掉防线确认会红** |
| **L1/L2** 信息泄漏 | 错误消息不再回显模型输出与底层异常 | 实测含 PII 的回复不再出现在 message 中 |
| **AC-24** 不可达且报错方向错 | 按 spec 行 9/行 4 拆分 11608 / 11605 | 新增 2 条回归用例 |
| 试抽取 reason 映射不一致 | 前后端 token 对齐，三语补键 | 后端 8 个 token 与前端表逐一对齐 |
| 审计模块筛选只接一半 | 补 `getModulesApi` 条目 + `systemIdEnum` 三语标签 | — |
| **我自己的回归**：`commit` 写在 session 上下文之外 | 移回 `async with` 内 | e2e 的「删除后重建」当场撞出 |

### 剩余（不阻塞）

- **M3** 唯一性查重在 `strip()` 之前 —— 前导空格可绕过（`'  business_license'`）
- **M5** design 承诺的 120 秒超时**代码里不存在**
- **并发**：两个管理员同时建同标识模板 → 双活（需库层唯一索引，与「软删可复用」的设计存在张力，值得单独决策）
- **记录列表过滤**（template_id/status/时间/翻页）无任何一层测试
- **M-2b 自动判定编排路径**（AC-18/AC-20）无测试
- 文档：design 引用不存在的 `record_query_service.py`、§6 仍写旧审计依赖、tasks.md 三处失真

---

## 本次审查最有价值的一条

**H1、H2、以及我自己引入的 commit 错位 —— 三条都穿过了当时全部 133 个绿灯单测。**

- H1 穿过，是因为测试把 `_resolve_model_id` mock 成直接返回整数，**被测的解析代码从未执行**；
- H2 穿过，是因为测试把 DAO 整体 mock 掉，**租户过滤与事务边界都不在测试范围内**；
- commit 错位穿过，同理（DAO 被 mock）。

三者都只在**真栈 + 真实数据形态**下暴露。已作为坑 18 写入 design §5。

**可操作的结论**：对「租户隔离」「事务提交」「配置解析」这三类边界，mock 掉 DAO/解析层等于不测。它们应当走 e2e，或至少保留真实 IO。
