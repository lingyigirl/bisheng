# Feature: F045 MiniMax TTS 适配（aiping / MiniMax 官方语音合成）

> **本文档定位 — 纯 What（需求口径，不随代码漂移）**
>
> spec 只回答 **做什么 / 验收标准 / 不做什么**。
> 所有 How（协议格式、解码逻辑、config 键名、文件清单）在 [design.md](./design.md) 与 [tasks.md](./tasks.md)。

**关联 PRD**: 无正式 PRD；需求来自用户现场选型（aiping 模型市场 + MiniMax 官方 T2A 协议实测）
**优先级**: P1
**所属版本**: v2.7.0
**依赖**: v2.6.0 LLM 管理（模型 CRUD / `BishengTTS` 工厂 / 工作台 TTS 调用链已上线）

> **范围边界**
> - **本次纳入**：
>   - `minimax` 服务商类型下的 TTS（文字转语音）客户端，支持 aiping 网关协议（主）与 MiniMax 官方 T2A 协议（兼容）
>   - 平台「模型管理」中 minimax 服务商可添加 TTS 类型模型，并可设为工作台默认 TTS
> - **本次明确排除**：
>   - minimax 类型下的 ASR —— aiping 无 ASR 能力；本地 ASR 已由 Qwen3-ASR 覆盖（不在本特性范围）
>   - 流式 TTS / 音色列表 UI（voice_id 手填即可，不做音色库管理页）
>   - minimax 服务商现有 LLM / embedding 行为改动（ChatMinimax、OpenAIEmbeddings 保持原样）
>   - 工作台 TTS 的音频存储链路（合成结果 → MinIO → URL 返回）改动 —— 纯复用

---

## 1. 用户故事

作为 **平台管理员**，
我希望 在模型管理中以 minimax 服务商类型添加 aiping（或 MiniMax 官方）的 TTS 模型并设为工作台默认，
以便 平台上的语音合成功能（工作台 TTS）能通过已采购的 aiping 账户正常出声音。

---

## 2. 验收标准

| AC-ID | 验收标准 |
|-------|---------|
| AC-01 | WHEN 管理员以 minimax 服务商类型（配置 Api Host + API Key）添加 `tts` 类型模型，THEN THE SYSTEM SHALL 走既有模型创建流程并**真实合成一次音频作为连通性测试**；测试通过则模型状态正常（status=0）、在线（online=true） |
| AC-02 | WHEN 调用该 TTS 模型合成文本，THEN THE SYSTEM SHALL 向服务商发起 MiniMax 原生格式请求：必含 `text`（待合成文本）与 `voice_setting.voice_id`（音色，取值自模型配置的 `voice` 字段）；`model` 取模型真实模型名 |
| AC-03 | WHEN 服务商返回 aiping 网关格式（JSON 中 `data.audio` 为 **base64** 编码音频），THEN THE SYSTEM SHALL 解码为音频字节并返回（供平台按既有链路存储/播放） |
| AC-04 | WHEN 服务商返回 MiniMax 官方 T2A 格式（`data.audio` 为 **hex** 编码音频），THEN THE SYSTEM SHALL 正确解码为相同语义的音频字节 |
| AC-05 | IF 服务商返回非 200 状态码 / 响应体缺 `audio` 字段 / 编码无法解码，THEN THE SYSTEM SHALL 抛出含服务商响应信息的异常，不得返回损坏的"音频"数据 |
| AC-06 | WHEN 调用方在合成时显式传入 `voice`，THEN THE SYSTEM SHALL 以调用方传入值为准；否则回退模型配置的 `voice` |
| AC-07 | WHEN 端点路径需要适配不同服务商（aiping 网关与官方 API 路径不同），THEN THE SYSTEM SHALL 支持按服务商配置覆盖端点路径，默认走 aiping 网关路径 |
| AC-08 | WHEN 在模型管理中选择 minimax 服务商，THEN THE SYSTEM SHALL 在模型类型下拉中提供 TTS 选项，且 TTS 模型的音色（voice）输入与必填校验与现有 openai/azure 类型保持一致 |
| AC-09 | WHILE 使用非 minimax 服务商类型的 TTS（openai / azure_openai / qwen），THE SYSTEM SHALL 行为与改动前完全一致（零回归） |
| AC-10 | THE SYSTEM SHALL 以单元测试覆盖 AC-02 ~ AC-07（协议请求体、两种编码解码、错误路径、voice 优先级、端点覆盖） |
| AC-11 | WHEN 管理员把该 minimax TTS 模型设为工作台默认 TTS 并调用工作台 TTS 接口合成文本，THEN THE SYSTEM SHALL 返回可访问的音频结果（沿用既有 MinIO 存储链路），端到端可用 |

---

## 3. 不在范围（明确不做的细节）

- 不做 minimax ASR 客户端（aiping 无 ASR 模型）
- 不做 TTS 历史记录 / 用量看板（复用既有 token 计费与审计）
- 不新增错误码、不新增数据库表与字段、不新增对外 API（沿用 `/api/v1/llm` CRUD 与 `/llm/workbench/tts`）
