# Release Contract — v2.7.0

> 本文件是版本级领域归属与全局约束的权威来源。
> **所有 spec.md 在动笔前必须先阅读本文件。**
> 每次 spec 评审时，必须对照本文件检查一致性。

---

## 表 1：领域对象归属

每个领域对象只能有一个 Owner Feature，负责定义该对象的写入行为
（创建、更新、删除）。其他 Feature 只能"读取"或"引用"该对象。

| 领域对象 | Owner Feature | 说明 |
|---------|--------------|------|
| LLMServer / LLMModel（`minimax` 服务商类型的 TTS 客户端注册与参数处理） | F045-minimax-tts-adapter | 在既有 `llm_server` / `llm_model` 表与 CRUD 之上，为 `minimax` 服务商类型新增 TTS 客户端注册（`MiniMaxTTSClient`）；**不拥有** LLMServer / LLMModel 对象本身（沿用 v2.5.0 LLM 管理归属），不新增表 / 字段 / DAO 入口 |
| —（无新增领域对象） | F045-minimax-tts-adapter | 本特性仅新增一个 TTS 协议客户端 + 一处注册 + 前端类型白名单放行；错误码复用既有 TTS 错误码（`common/errcode/server.py` 的 Tts* 系列），不新增错误码 |

**规则**：
- 非 Owner Feature 的 AC 中不得出现其他对象的"创建/修改/删除"行为，只能"读取"或"调用" Owner 的 Service
- 新增领域对象时必须先更新本表

---

## 表 2：跨 Feature 不变量（INV-N）

全局业务约束，任何 spec 的 AC **不得与之矛盾**。

| ID | 不变量描述 | 涉及领域对象 | 来源 spec |
|----|-----------|------------|---------|
| INV-8 | 系统默认模型配置（知识库 / 工作台 / 助手 / 评估 / 工作流）中的模型 ID 必须是本租户内可见（自有或 Root 共享）的 `llm_model` 行，且其 `model_type` 与配置槽位匹配（如工作台 `asr_model` 必须为 `asr` 类型、`tts_model` 必须为 `tts` 类型） | llm_model, tenant_system_model_config | F022（v2.6.0 遗留） |
| INV-9 | 服务商配置中的密钥类字段（`openai_api_key` 等）在读取响应中必须脱敏；写入路径沿用既有 CRUD 与审计 | llm_server | v2.6.0 LLM 管理 |

> 注：INV-8 / INV-9 为 v2.6.0 既有约束的回填登记（原 v2.6.0 release-contract 未编号记录，此处补记，与 F022/F020 行为一致）。编号续接 v2.6.0 的 INV-1 ~ INV-7。F045 不改动这些不变量，仅在其框架内注册新的 TTS 客户端。

**规则**：
- 新增不变量：先在此表追加，再写 AC
- 修改不变量：必须列出 Impacted Specs 清单，逐一回写并重新评审
- 冲突检测：若 AC 与不变量矛盾，spec 评审不通过

---

## 表 3：Feature 依赖图

| Feature | 依赖（必须先完成） | 说明 |
|---------|-----------------|------|
| F045-minimax-tts-adapter | v2.6.0 LLM 管理（F020/LLM CRUD 已上线） | 依赖既有 `/api/v1/llm` CRUD、`BishengTTS` 工厂与工作台 TTS 调用链（`/llm/workbench/tts`） |

---

## 已分配模块编码（MMMEE）

> 新 Feature 分配错误码时，必须检查此表避免冲突。

| 模块编码 (MMM) | 模块 | Owner Feature |
|----------------|------|---------------|
| —（F045 不新增错误码，复用 `common/errcode/server.py` Tts* 系列） | — | — |

---

## 变更历史

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-08-26 | 初始版本：登记 F045-minimax-tts-adapter | — |
