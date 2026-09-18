# Code Review Report — 056-llm-guardrail

**Feature**: F056 大模型安全围栏
**Review scope**: `git diff ced66de9e~1..396ffe1d4` —— F056 全部 4 个提交（44 文件，3506 insertions）
**Base**: `ced66de9e~1`
**Review date**: 2026-09-18
**Reviewer note**: F059（平台强制词库）已于 2026-09-17 落地并改动了本特性的两个测试文件与检测链路（两层模型）。本次审查针对的是**当前现状**，非 F056 提交时的快照。

---

## Summary

> **⚠ 本表为第二轮（三维度补审 + 我的订正）后的结果，与初版不同。** 初版判定「代码维度全部通过」，**该判定有误** —— 见 H3/H4。

| Dimension | High | Medium | Low | Status |
|-----------|------|--------|-----|--------|
| 1. 边界条件 | 2 (H3/H4) | 3 | 2 | **NEEDS_FIX** |
| 2. 权限与认证 | 1 (H8) | 2 | 0 | **NEEDS_FIX** |
| 3. 并发安全 | 0 | 0 | 1 | PASS |
| 4. 信息泄漏 | 1 (H6) | 2 | 2 | **NEEDS_FIX** |
| 5. 测试覆盖 | 1 (H7) | 2 | 1 | **NEEDS_FIX** |
| 6. 代码风格 | 0 | 0 | 1 | PASS |
| 7. 文档同步 | 1 (H5) | 3 | 2 | **NEEDS_FIX** |
| 8. 出口一致性（补审新增） | 1 (H6) | 3 | 0 | **NEEDS_FIX** |

**首轮已闭合**：H2（design 未同步 F059 两层现状，已回写）、M1（AC-18 无测试，已补 `test_f036_regression.py`）、M3。
**首轮 H1 仍开放**：核心 AC 的真实链路验证依赖未执行的手动清单。

## Overall: **NEEDS_FIX**（1 条 HIGH 待处理）

代码本身（边界、权限、并发、泄漏、风格）**全部通过**，且最高风险的两处经我**实际执行穷举验证**确认正确。
两条 HIGH 都出在**测试覆盖**与**文档同步**上。

**处置进度（2026-09-18）**：

| 项 | 处置 |
|---|---|
| **H2** design 未同步 F059 两层现状 | ✅ **已回写**（§4.2 `source` 与行粒度、§4.3 双 matcher、§5 坑 13-15、§6.1 双端点与脱敏） |
| **M3** §7 测试落点滞后 | ✅ 随 H2 一并处理 |
| **M1** AC-18 无测试 | ✅ **已闭合**（新增 `test_f036_regression.py` 15 条，变异测试 4/4 验证有效） |
| **H1** 核心 AC 的真实链路验证依赖未执行的手动清单 | ❌ **仍开放** —— 需能连中间件的环境，代码侧无法替代 |
| **M2** F056/F059 测试未分层 | 保留（不阻塞） |

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

### ~~mask 打码不泄漏词头~~ —— **原判有误，实为 HIGH**（见下 H3 与订正说明）

> **⚠ 本节结论已被推翻（2026-09-18）。** 原判断基于一次**词表构造不足**的穷举，见文末「订正：一次被构造不足的穷举」。真实缺陷见下方 **H3**。

`_resolve_emit_len` 的 `hold = min(matcher.max_word_len - 1, buf_len)` 保证缓冲窗口被最长词长界住，**不会无界增长**；`tail_state` 回退用 `prior_state` 而非 `state_after`（`emit_len==0` 时全量重扫），注释解释了为何不能反过来。**这两点仍成立**，但不足以保证「不泄漏」。

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

## HIGH（第二轮：边界/并发 + 测试/风格/文档三维度补审，2026-09-18）

> 以下 6 条来自两个补审 agent（边界与并发、测试与文档），**全部经实际执行确认**（agent 用真实 `ChatGuardService` + 真实 `build_output_guard_for_callback` + 随包 `words.txt` 2809 词驱动，探针文件已删）。其中 H3 推翻了本报告初版的一个结论。

### H3. mask 模式在 emit 边界明文吐出命中词的大部分 —— `in_progress` 忽略了 AC 失败链

- **位置**：`sensitive_word/domain/services/ac_automaton.py:59`（`return node is not self.root and bool(node.children)`），消费点 `chat_guard.py:552` / `:571`
- **缺陷**：trie 节点是叶节点 ⟺ 有词在此结束，故 `bool(node.children)` 回答的是「**本节点**能否延伸」，但文本后缀完全可能经 **fail 链**仍是更长模式的活前缀。此时 `hold = 0`，整个缓冲被发出；当那个更长的词在下一分片完成时，`_mask` 因跨度起点在边界之前（`start >= 0` 守卫）而**丢弃它**。
- **实测（我独立复现，真实内置词表）**：
  ```
  词表 = ['习尽评吓台', '台独万岁']    ← 均为随包 words.txt 真实词条
  流式输入 '习尽评吓台独万岁'，逐字符分片
  输出     '*****独万岁'
  →  '台独万岁' 有 3/4 字符明文可读
  ```
  agent 另报 `jb` / `bbc中文网` 一组可泄漏 5/6，最坏 14/15。
- **影响面**：内置 2809 词中 **1530 个**词以「叶节点且 fail 链前缀仍活」的形态结束。
- **违反**：design 坑 5「命中词永不跨 emit 边界被拆开发出」+ AC-11。
- **状态**：**CONFIRMED**（agent 与我各自独立执行）。

### H4. mask 模式下非流式路径的扣留尾部从不冲刷 → 回答被静默截断甚至整个消失

- **位置**：`common/chat/client.py:395-401`、`open_endpoints/api/endpoints/assistant.py:109-111`
- **缺陷**：两条路径把整段回答作为**一个** chunk 喂给守卫，而此前那次 `chunk == ''` 调用是空操作（尚无累积内容，`guardrail.py:67` 不冲刷），随后却直接 `answer = guarded.safe_text`。mask 模式在回答尾部是活前缀时会扣留 `min(max_word_len-1, buf_len)` 个字符，而**该尾部被丢弃** —— 既不发出也不打码。
- **实测**：回答 `上述内容仅供学习参考`（10 字符，结尾 `考` 是 `考答案`/`考题` 的活前缀）→ `safe_text == ''`，用户看到**空气泡**，且空串被写入聊天历史。更长的回答最多丢失末 15 字符（`max_word_len=16`）。
- **影响面**：内置词表中 1018 个不同字符是活前缀，故任何以之结尾的回答都会被截断。命中所有非流式模型 + 任何 `stream=false` 的 `/api/v2/...` 调用方。
- **状态**：**CONFIRMED**（agent 经真实 wrapper 执行；我独立复现了机制）。

### H5. 工作流侧历史落库保存的是**未过滤原文**；design 坑 6 与 §7 的断言是错的

- **位置**：`design.md:154`（坑 6）、`:191`（§7）
- **缺陷**：design 称「输出截断后 chat 历史保存的是截断后文本（`send_response` 带 `add` 参数负责入库）」—— **`send_response` 根本没有 `add` 参数，也从不写库**（`common/chat/clients/base.py:129-133`，函数体只 `send_json`）。
- **真实链路**：`worker/workflow/redis_callback.py:807-829` `on_stream_over` → `:822 save_chat_message(...)` → `:545 ChatMessageDao.insert_one(...)`，**发生在 `:829 self.send_chat_response(...)` 之前**，拿的是引擎侧原文。客户端侧改写（`workflow_client.py:215`）只改 ws 帧，**发生在落库之后**。
- **后果（合规级）**：工作流链路历史里存着被围栏拦下的违规原文，与 design 承诺「历史文本本身不含违规内容」完全相反。助手链路是对的（`common/chat/client.py:391-403` 先改写再 `:434 add_message` 落库）——**两条链路行为不一致，design 把助手的语义当成了通用语义**。
- **状态**：**CONFIRMED**（agent 逐层读码追踪）

### H6. 工作流 `OutputMsg` 帧完全绕过输出围栏，命中词原样到达前端并落库

- **位置**：`common/chat/clients/workflow_client.py:184-201`（`_guard_output_event`），触发点 `workflow/callback/llm_callback.py:154-173`
- **缺陷**：`:201` 对 `category != StreamMsg` 的事件**直接放行**。`llm_callback.py:154` 的判据是 `if self.stream and self.output_len > 0`，否则走 `:169 on_output_msg`（其注释自承 "This happens when the cache is hit"）→ `redis_callback.py:772` 发 `category='output_msg'` 帧。
- **后果**：命中缓存的 LLM 节点、或未开流式的节点，其输出**完全不经输出检测**即送前端，且 `redis_callback.py:774` 同样落库。
- **实测**：构造 `output_msg` 帧走真实 `_workflow_run` → `OUTPUT-MSG over output_msg len=8 leaked_raw=True masked=False`。
- **design §5 未记载**（其声称 12 条坑，无此条）。
- **状态**：**CONFIRMED**（agent 执行探针）

### H7. 输出方向的 `business_type` 无任何测试锚定；改坏后 143 个测试全绿

- **位置**：`chat_guard.py:261`（`start_output_guard` 读 `CHAT_OUTPUT`）、`test/sensitive_word/test_chat_guard.py` 的 autouse `stub_hit_log` 夹具
- **缺陷**：夹具 stub 是 `lambda **kwargs: _policy(...)`，**把 `business_type` 丢弃不校验**，故策略查询传什么都「过」。
- **实测（变异）**：把 `:261` 的 `CHAT_OUTPUT` 改成 `CHAT_INPUT`（输出方向误用输入词表）→ **143 passed**。
- **后果**：AC-09「输出使用输出方向**独立**配置的词表」在自动化层面**实质零覆盖**。与 F058 报告那条同源：测试 mock 掉了被测层的判别依据。
- **状态**：**CONFIRMED**（agent 变异实验）

### H8. 第 4 个对话入口 `/api/v1/workstation/chat/completions` 完全没有围栏

- **位置**：`workstation/api/endpoints/chat.py:97` → `workstation/domain/services/chat_service.py:2010 stream_chat_completion`
- **缺陷**：整条链路零 guardrail 引用，既不在 spec 的「任一对话入口」枚举里，也不在 design §5 的入口清单里。
- **后果**：AC-05「任一对话入口」不成立；从 workstation 发起的对话输入输出均不受检。
- **状态**：PLAUSIBLE（已确认无引用、已确认是 chat 入口；未实跑该链路）

### 相关 MEDIUM（详见 agent 原始记录）

- **M-新1**：4 个 ws 端点的 `input_guard=` / `output_guard=` 实参**可整体删除而 143 测试全绿** —— 无人断言 `dispatch_client` 真收到了 kwargs。AC-05/AC-17 的接线无回归保护。
- **M-新2**：命中日志端点的权限依赖从未被求值（测试绕开 FastAPI DI），降级依赖后全绿。
- **M-新3**：spec/design 写的 `POST /api/v2/chat/completions` **不存在**，实际是 `POST /api/v2/assistant/chat/completions`（`api/router.py:101` + open_endpoints 的 `assistant` 前缀）。按 spec 写集成代码会 404。
- **M-新4**：design 四处仍称输出检测落点是 `send_response`，实现重构后已改为事件层三处；§6.2 记的是一条**假的隐性契约**（这正是 H6 漏掉 `output_msg` 的根因）。
- **M-新5**：`auto_reply` 在每次 blocked 调用后被重复追加（`state.blocked` 永不清除），`full_text()` 实测返回 `'xx[BLOCKED][BLOCKED][BLOCKED]'`。
- **M-新6**：工作流的 `full_text()` 跨整个 run 累积，却重写**每一个** `StreamMsg` 的 `end` 帧 → 两个流式节点时第二个气泡会带上第一个的回答。
- **M-新7**：任何未经 `''` 冲刷就结束的流（用户 Stop、工作流错误、非 `stream_msg` 结束）丢尾部。
- **M-新8**：终局收口逻辑三处复制，`workflow_client.py:215` 少了空值兜底。
- **M-新9**：design 坑 3 对 `clear_cache` 的因果描述夸大（AC-03 实际由缓存键含 `words_digest` 保证）。
- **LOW**：同步 DB 读发生在事件循环上（K4 说「检测路径不查库」）；HTTP 路径 `chat_id` 传 `None` 致日志 `chat_id=NULL`；`str.lower()` 长度不守恒（`'İ'` → 2 字符）会使打码错位；2 处新增 RUF003。

### 已核验无问题（避免重复排查）

进程级自动机缓存在线程池下**安全**（8 线程压测：8000 次并发扫描 × 400 轮 `_get_automaton`/`clear_cache`，0 错误）；每会话守卫状态隔离正确；`capture_at` / `_rewind_scan_state` 在每个切点都正确；尾部有界（实测最坏 11 字符）；去重水位单调安全；block 跨分片取两层最早命中正确；空/纯空白分片处理正确；分页边界正确。

## HIGH（首轮）

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

### ~~M1. [测试覆盖] AC-18（F036 既有场景不受影响）无任何自动化测试~~ —— **已闭合（2026-09-18）**

- **位置**: `test/sensitive_word/test_f036_regression.py`（新增，15 条）
- **事实**: spec AC-18 要求「知识库文件解析与订阅文章两个既有场景行为不变」。原无单测也无 e2e，唯一依据是**未执行**的手动清单第 6 项。
- **风险**: 本特性重构了共享设施 —— `sensitive_word_policy_service.py` 118 行改动（`check_texts` 改走新的 `build_matcher` 门面）、`ac_automaton.py` 60 行改动（`find_all` 重写为委托 `find_all_matches`），两者都是 F036 的**共用**组件。
- **处置**: 补 15 条回归用例，覆盖三块此前无覆盖的面 ——
  1. `check_texts` 对外契约（每输入一结果且保序、非生效路径仍展开成 N 条 disabled、`auto_reply` 回退、`max_hits` 上限、大小写策略与上报词形）；
  2. **订阅文章路径**（`ChannelService.apply_article_sensitive_reviews`）——此前**零测试**；
  3. `find_all` 与 `find_all_matches` 的计数等价性。
- **有效性验证（变异测试，4/4 全部被抓）**：

  | 注入的回归 | 变红用例 |
  |---|---|
  | 非生效路径返回 `[]` 而非逐条 disabled | 2 failed |
  | 忽略 `max_hits` 上限 | 1 failed |
  | 文章评审丢弃 `hits` | 2 failed |
  | `find_all` 不再累计重复计数 | 2 failed（并连带既有用例） |

  全部变异已还原（`git status` 干净，`test/sensitive_word/` 158 passed）。

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
| AC-18 | F036 既有场景不变 | `test_f036_regression.py`（15 条） | 清单 #6 | ✅ 已补（原 M1） |

**7 条 AC 的真实链路验证依赖未执行的手动清单。**

---

## 验证（本次实测）

| 项 | 命令 | 结果 |
|---|---|---|
| F056 单测（6 个归属文件） | `pytest test_ac_automaton_incremental test_chat_client_input_guard test_chat_client_output_guard test_chat_guard test_hit_log_query test_open_endpoints_guard` | **52 passed** |
| F057 单测（对照） | 6 个 f057 文件 | 52 passed |
| 跨分片穷举 | 直接驱动 `find_all_matches`，12 单切分 + 13 粒度 | 与全量扫描**逐项一致** —— 匹配**总能被找到** |
| ~~mask/block 泄漏穷举~~ | ~~25 种分片 × 2 动作~~ | ~~零泄漏~~ → **结论作废**：穷举用的词表构造不足，未含「叶节点 + fail 链活前缀」结构。见 H3 |
| ruff（核心新增文件） | `ruff check` × 5 | 净增 **0** |
| 全目录混合跑 | `pytest test/sensitive_word/ test/knowledge/ test/permission/` | 106 failed / 1485 passed —— 失败全部落在需中间件的模块（space service / openfga / f006 migration），**与 F056/F057 无关**，两特性各自子集全绿 |

---

## 订正：一次被构造不足的穷举（2026-09-18）

本报告初版断言「mask 打码零泄漏」，并据此判定边界维度 PASS。**该结论错误**，源自我的验证方法本身有缺陷 —— 记在这里，因为它比结论本身更有用。

**我做了什么**：构造词表 `['绝密文件', '密码', 'ab']`，遍历全部单切分点与全部固定分片粒度（共 25 种），检查输出是否含完整敏感词 —— 零泄漏。

**为什么它必然零泄漏**：mask 泄漏的触发条件是**「某词在 trie 叶节点结束，且该节点的 fail 链上仍存在活前缀」**。我那个词表里：

- `绝密文件` 结尾的节点 fail 链上没有活前缀
- `ab` 同理

**一个不包含触发结构的词表，无论切多少刀都不会泄漏。** 我的 25 种分片不是 25 次独立验证，而是**同一个未触发条件下测了 25 遍**。测试全绿，因为它测的根本不是那个属性。

换个词表立刻暴露（`习尽评吓台` + `台独万岁`，两条都是随包 `words.txt` 的真实词条）：

```
输出 '*****独万岁'   →  '台独万岁' 3/4 字符明文可读
```

**与 F058 那条教训的关系**：058 报告记的是「mock 掉被测层等于不测」。这里是同一模式的另一种形态 —— **被测对象是真实的，但输入空间没有覆盖到触发条件**。mock 掉的测试和构造不足的测试，都会以「全绿」的形式给出虚假信心。

**可操作的结论**：对「穷举验证」这类结论，必须先证明**词表/输入里存在能让该缺陷发生的结构**，否则穷举的每一种分片都只是同一条件的重复。具体到本特性：验证 mask 不泄漏，词表里**必须**含一对「A 的某个后缀是 B 的前缀」的词。

---

## 审查方法说明

- 4 个并行审查 agent 于审查中途被终止（耗时过长未返回），**本报告结论全部来自我本人的直接验证**，无 agent 转述内容。
- 遵循本项目在 F058 报告中确立的教训：**不以测试全绿作为证据**。上表「通过验证的部分」均标注了实际执行方式。
- 期间为验证测试有效性，曾临时改动 `department_service.py` 的 `_dept_node_dict` 字段名，确认 `test_node_shape_matches_the_children_endpoint` 会变红后**已完整还原**（`git status` 干净，11 passed 复核）。
