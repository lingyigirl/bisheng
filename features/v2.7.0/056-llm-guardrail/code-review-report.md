# Code Review Report — 056-llm-guardrail

**Feature**: F056 大模型安全围栏
**Review scope**: `git diff ced66de9e~1..396ffe1d4` —— F056 全部 4 个提交（44 文件，3506 insertions）
**Base**: `ced66de9e~1`
**Review date**: 2026-09-18
**Reviewer note**: F059（平台强制词库）已于 2026-09-17 落地并改动了本特性的两个测试文件与检测链路（两层模型）。本次审查针对的是**当前现状**，非 F056 提交时的快照。

---

## Summary

| Dimension | High | Medium | Low | Status |
|-----------|------|--------|-----|--------|
| 1. 边界条件 | 0 | 0 | 2 | PASS |
| 2. 权限与认证 | 0 | 0 | 0 | PASS |
| 3. 并发安全 | 0 | 0 | 1 | PASS |
| 4. 信息泄漏 | 0 | 0 | 0 | PASS |
| 5. 测试覆盖 | 2 | 2 | 0 | **NEEDS_FIX** |
| 6. 代码风格 | 0 | 0 | 0 | PASS |
| 7. 文档同步 | 1 | 2 | 0 | **NEEDS_FIX** |

## Overall: **NEEDS_FIX**

代码本身（边界、权限、并发、泄漏、风格）**全部通过**，且最高风险的两处经我**实际执行穷举验证**确认正确。
两条 HIGH 都出在**测试覆盖**与**文档同步**上：核心 AC 依赖一份**从未执行**的手动清单，且 design 未同步 F059 引入的两层现实。

---

## 通过验证的部分（非静态推断，均为实跑）

### 跨分片增量匹配 —— 正确

最高风险逻辑。`ACAutomaton.find_all_matches` 携带扫描状态跨分片续匹配（design 决策 3 / 坑 1）。

**验证方式**：直接驱动自动机，对 `敏感词 / 敏感 / ab / abc` 词表，遍历**所有**单切分点（12 种）与**所有**固定分片粒度（k=1..13），比对分片扫描与全量扫描：

```
whole : [('ab',2), ('abc',1), ('敏感',2), ('敏感词',1)]
k=1   : 同上   OK      k=3 : 同上   OK
k=2   : 同上   OK      k=5 : 同上   OK
```

任意粒度结果**逐项一致**，跨分片词无一漏检。重叠模式全报符合 design 坑 5 的规定语义。

### mask 打码不泄漏词头 —— 正确

design 坑 5 的核心安全属性：永不输出半个敏感词。

**验证方式**：25 种分片方式（12 单切分 + 13 固定粒度）× mask/block 两种动作，检查输出中是否出现完整敏感词：

```
MASK:  12 single-cut splits + 13 fixed chunkings over 13 chars
  leaks found: NONE
BLOCK: leaks found: NONE
```

`_resolve_emit_len` 的 `hold = min(matcher.max_word_len - 1, buf_len)` 保证缓冲窗口被最长词长界住，**不会无界增长**；`tail_state` 回退用 `prior_state` 而非 `state_after`（`emit_len==0` 时全量重扫），注释解释了为何不能反过来。

> 附注：流真在词中间结束时（如只生成了 `绝密文`），原样输出是**正确行为** —— 敏感词从未生成完，不存在可泄漏的完整词。

### 入口一致性 —— 无遗漏

design K2 的核心风险是「任一入口漏埋点 = 围栏有洞」。全部 4 处 `dispatch_client` 调用点均已同时接线输入/输出守卫：

| 入口 | 位置 |
|---|---|
| v1 助手 ws | `api/v1/assistant.py:235` |
| v1 工作流 ws | `api/v1/workflow.py:180` |
| v2 OpenAPI 助手 ws | `open_endpoints/api/endpoints/assistant.py:340` |
| v2 OpenAPI 工作流 ws | `open_endpoints/api/endpoints/workflow.py:169` |

`/api/v2/chat/completions`（HTTP，不走 ChatManager）按其独立实现单独埋点（`open_endpoints/.../assistant.py:82-83`）。

### C1 / RULE-1 分层 —— 合规

`common/chat/` 内**无** `sensitive_word` 领域导入（仅 3 处注释与类型说明）。检测逻辑由 api 层工厂 `api/services/guardrail.py` 构造闭包后注入，与 design 决策 2 一致。

### 权限与信息泄漏 —— 通过

| 检查项 | 结果 |
|---|---|
| 租户日志端点鉴权 | `Depends(UserPayload.get_tenant_admin_user)` ✅ |
| 跨租户日志端点鉴权 | `Depends(require_global_super_admin)` ✅ |
| 平台词命中词脱敏 | 在**服务端**置空 `hits`（`_to_hit_log_item`），非前端隐藏 —— 前端隐藏仍会把词发上网 ✅ |
| 脱敏测试 | `test_hit_log_redaction.py` 7 条，含「混合页只脱平台行」「NULL source 按 tenant 处理」✅ |
| INV-11 留痕边界 | 模型无原文列，`hits` 仅 `{word, count}`；测试 `test_hit_log_recorded_without_raw_text` 锁定 ✅ |
| 租户过滤 Row 元组 | `alist` 用 `row[0]` 解包（design 坑 11）✅ |
| 错误码 | 11201/11202 与契约一致，三语 `api_errors` 齐备 ✅ |
| 前端硬编码中文 | 新增 3 个前端文件均为 0，i18n 合规 ✅ |
| 前端规则 | 无 `backdrop-filter`；无默认导出；均 <600 行；`import axios from "../request"` 是包装模块（同目录 33 个文件同写法）✅ |

### 代码风格 —— 通过

新增/改动核心文件的 ruff 违规数**净增 0**（`guardrail.py`、`chat_guard.py`、`hit_log.py`、`ac_automaton.py`、`hit_logs.py` 全部 `All checks passed`）。

---

## HIGH

### H1. [测试覆盖] AC-05 / AC-10 / AC-11 / AC-12 / AC-14 的真实链路验证从未执行

- **位置**: `features/v2.7.0/056-llm-guardrail/e2e-checklist.md:23`
- **事实**: 该清单 7 项全部标注「待执行」，`验证记录` 表为空（`| — | — | — | 待执行 |`）。
- **后果**: 清单里这 7 项恰好覆盖 **ws 输入拦截（AC-05）、流式输出截断（AC-10）、打码（AC-11）、无改写直通（AC-12）、HTTP 流式（AC-14）** —— 即本特性的**全部核心用户可见行为**。自动化 e2e 仅覆盖 AC-01/02/03/06/07/08/15/17（且需活后端，本次 ConnectError 未执行），单测覆盖 AC-02/05/06/09/10/13/16/17 的分支逻辑。
- **判定**: 单测覆盖了这些 AC 的**分支逻辑**（我实跑确认 `test_chat_client_input_guard.py` 确实断言"不进入分发"+话术、`test_chat_client_output_guard.py` 有 6 条 mask 用例），但**真实 ws/流式链路**未验证。design §7 明确把这部分划给手动清单。
- **建议**: 在能连中间件的环境执行清单并回填；或明确接受「单测覆盖分支 + 手动清单待执行」作为本期交付口径并记入 tasks.md，**不要留空表**。

### H2. [文档同步] design.md 未同步 F059 引入的两层检测模型

- **位置**: `features/v2.7.0/056-llm-guardrail/design.md` §4.3 模块职责、§6.1 对外契约
- **事实**: F059（2026-09-17）落地后，检测链路已从单层变为**两层**（租户层 + 平台强制层），但 design 仅在 K3 与 §7 提及 F059 的「前提」与「短路原则」，下列现状均未登记：
  - `OutputGuardState` 现持有**双** matcher / **双** tail_state / **双** last_counted_pos（`chat_guard.py:62-75`）
  - 命中日志**每层各写一行**，新增 `source` 字段（tenant/platform）
  - 新增跨租户端点 `GET /api/v1/sensitive-word-hit-logs/all`（`hit_logs.py:50`）—— §6.1 只登记了租户端点
  - 租户视图对平台行**服务端脱敏**（INV-10「平台词库内容不得被反向获取」的落地手段）
  - platform 层的 action/auto_reply **优先于**租户层（spec AC-12 口径）
- **后果**: 接手者读 design §4.3「`chat_guard.py` —— 对话围栏门面」会以为只有一个匹配器；读 §6.1 会漏掉一半端点。这正是本项目反复出现的文档漂移模式（F058 的 `reason` 枚举表失真直接导致一个 high 级前端缺陷）。
- **建议**: 按 F059 现状回写 §4.3 / §4.2（`source` 字段）/ §6.1（`/all` 端点 + 脱敏口径），并在修订历史追加条目。

---

## MEDIUM

### M1. [测试覆盖] AC-18（F036 既有场景不受影响）无任何自动化测试

- **位置**: `test/`（无对应用例）
- **事实**: spec AC-18 要求「知识库文件解析与订阅文章两个既有场景行为不变」。既无单测也无 e2e，唯一依据是**未执行**的手动清单第 6 项。
- **风险**: 本特性重构了共享设施 —— `sensitive_word_policy_service.py` 118 行改动、`ac_automaton.py` 60 行改动，两者都是 F036 的**共用**组件。回归风险是真实的，而非形式上的。
- **建议**: 补一条针对 `check_texts`（F036 原入口）的回归用例，锁定其行为不因新增 `build_matcher` 门面而改变。

### M2. [测试覆盖] `test/sensitive_word/` 中 F056 与 F059 的测试未按特性分层

- **事实**: 该目录混有两特性的测试（如 `test_platform_matcher.py` 17 条、`test_platform_word_service.py` 30 条属 F059；`test_chat_guard.py` 与 `test_chat_client_output_guard.py` 被 F059 改过）。
- **后果**: 难以判断「F056 的测试是否仍全绿」—— 跑目录得到的是混合结果。
- **建议**: 不强制拆分（目录划分是合理的），但在 tasks.md 中记明哪些文件已被 F059 修改及原因。

### M3. [文档同步] design §7「测试与可观测」与现状不符

- **位置**: `design.md:190-198`
- **事实**: §7 描述的测试落点是 F056 时的形态（单层、无 platform 用例）；现状已有 `test_chat_guard_platform.py`(23)、`test_platform_matcher.py`(17) 等 F059 新增套件，§7 未提及。
- **建议**: 随 H2 一并回写。

---

## LOW

- **L1. [边界条件]** `open_endpoints/.../assistant.py:82-83` 的 `if tenant_id is not None` 分支使守卫在无租户上下文时**静默跳过**。已核实当前**不可达**（`get_default_operator()` 在第 54 行即 `set_current_tenant_id`，早于此处读取），属防御性代码。但若未来该端点的鉴权改造为不经过 `get_default_operator`，围栏会**静默失效**而非报错。建议加一行注释说明该前提。
- **L2. [边界条件]** `_resolve_emit_len` 的 `hold = min(max_word_len - 1, buf_len)` 在 `max_word_len <= 1` 时 `hold` 为 0，即单字符词表下不缓冲。对本场景无语义问题（单字符词在到达时即已完成，无需跨分片），但与「永不输出半个词」的表述相比不够直白。建议注释点明。
- **L3. [并发安全]** `_automaton_cache` 为普通 dict，读写依赖 GIL 原子性；`clear_cache()` 清空整个缓存。并发下最坏情况是重复构建自动机（无害）。在途流持有已构建的 matcher，**不受缓存清空影响**（符合 design 决策「新 matcher 需重建」的口径）。当前规模下可接受，记此备查。

---

## AC 覆盖矩阵

| AC | 内容 | 自动化 | 手动清单 | 判定 |
|---|---|---|---|---|
| AC-01 | 策略保存/独立配置 | e2e + `test_chat_guard.py` | — | ✅ |
| AC-02 | 未启用则零检测 | e2e + `test_chat_guard.py` + input_guard | — | ✅ |
| AC-03 | 修改立即生效 | e2e + `test_chat_guard.py` | — | ✅ |
| AC-04 | 租户策略隔离 | e2e（标注需多租户） | 清单 #7 | ⚠️ 单租户环境未实测 |
| AC-05 | 输入命中不执行 + 话术 | `test_chat_client_input_guard.py`（断言不进入分发） | 清单 #1 | ⚠️ 真实 ws 未验证（H1） |
| AC-06 | 未命中正常执行 | e2e + input_guard + open_endpoints_guard | — | ✅ |
| AC-07 | 输入命中写日志 | e2e + `test_chat_guard.py` | — | ✅ |
| AC-08 | HTTP 400 + 11201 | e2e + `test_open_endpoints_guard.py` | — | ✅ |
| AC-09 | 输出流式实时检测 | `test_chat_guard.py` + output_guard | — | ✅ |
| AC-10 | 命中即截断 + 话术收尾 | output_guard + automaton | 清单 #2 | ⚠️ 真实流式未验证（H1） |
| AC-11 | mask 打码继续 | `test_chat_guard.py`（6 条 mask 用例） | 清单 #3 | ⚠️ 真实流式未验证（H1） |
| AC-12 | 未命中逐字一致 | `test_chat_guard.py` passthrough 多处 | 清单 #4 | ⚠️ 真实链路未验证（H1） |
| AC-13 | 输出命中写日志 | `test_chat_guard.py` | — | ✅ |
| AC-14 | HTTP 流式同口径 | `test_open_endpoints_guard.py` | 清单 #5 | ⚠️ 真实 SSE 未验证（H1） |
| AC-15 | 日志按条件筛选 | e2e + `test_hit_log_query.py` | 清单 #6 | ✅ |
| AC-16 | 日志失败不影响主流程 | `test_chat_guard.py` | — | ✅ |
| AC-17 | 未配置行为不变 | e2e + input_guard | — | ✅ |
| AC-18 | F036 既有场景不变 | **无** | 清单 #6 | ❌ 无覆盖（M1） |

**7 条 AC 的真实链路验证依赖未执行的手动清单。**

---

## 验证（本次实测）

| 项 | 命令 | 结果 |
|---|---|---|
| F056 单测（6 个归属文件） | `pytest test_ac_automaton_incremental test_chat_client_input_guard test_chat_client_output_guard test_chat_guard test_hit_log_query test_open_endpoints_guard` | **52 passed** |
| F057 单测（对照） | 6 个 f057 文件 | 52 passed |
| 跨分片穷举 | 直接驱动 `find_all_matches`，12 单切分 + 13 粒度 | 与全量扫描**逐项一致** |
| mask/block 泄漏穷举 | 25 种分片 × 2 动作 | **零泄漏** |
| ruff（核心新增文件） | `ruff check` × 5 | 净增 **0** |
| 全目录混合跑 | `pytest test/sensitive_word/ test/knowledge/ test/permission/` | 106 failed / 1485 passed —— 失败全部落在需中间件的模块（space service / openfga / f006 migration），**与 F056/F057 无关**，两特性各自子集全绿 |

---

## 审查方法说明

- 4 个并行审查 agent 于审查中途被终止（耗时过长未返回），**本报告结论全部来自我本人的直接验证**，无 agent 转述内容。
- 遵循本项目在 F058 报告中确立的教训：**不以测试全绿作为证据**。上表「通过验证的部分」均标注了实际执行方式。
- 期间为验证测试有效性，曾临时改动 `department_service.py` 的 `_dept_node_dict` 字段名，确认 `test_node_shape_matches_the_children_endpoint` 会变红后**已完整还原**（`git status` 干净，11 passed 复核）。
