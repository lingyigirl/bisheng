# Design: F045 MiniMax TTS 适配（aiping / MiniMax 官方语音合成）

> **本文档定位 — 现状快照（Why this How）**
> `spec.md` 回答做什么；本文回答为什么这么实现。实现变化时覆盖更新本文档。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-08-26

---

## 1. 目标与非目标

- **目标**：让平台在 `minimax` 服务商类型下支持 TTS（文字转语音），对接 aiping 网关（主目标，已实测）并兼容 MiniMax 官方 T2A API，把语音合成接入既有工作台 TTS 链路。
- **非目标**：minimax ASR；流式合成；音色库管理 UI；改动 minimax 的 LLM / embedding 行为（M2.7 已改为 OpenAI 兼容，与 TTS 无关）；改动工作台 TTS 的 MinIO 存储链路。

## 2. 关键约束

- **协议实测结论（2026-08-26 现场验证，本设计的事实基础）**：
  - aiping 网关 TTS 端点 `POST {base}/audio/speech`，请求体为 MiniMax 原生 T2A 风格：必填 `text`（**OpenAI 风格的 `input` 会被 422 拒**）；顶层 `voice` 字段被网关**忽略**（直接报 "MiniMax TTS business error: invalid params, empty field"），必须用 `voice_setting.voice_id`；返回 `{"data": {"audio": "<base64 mp3>"}}`。
  - MiniMax 官方 T2A API（`POST {base}/t2a_v2`）请求体同构，但 `data.audio` 为 **hex** 编码。
  - 因此本客户端**不能复用 OpenAITTSClient**（其硬编码 openai SDK 的 `input` 字段 + 二进制响应解析）。
- 遵循 `docs/constitution.md` C1–C7：客户端放 `core/ai/tts/`（与既有三客户端同层，core 不依赖 domain/api）；注册点仍在 `bisheng/llm/domain/llm/tts.py` 的 `_tts_client_type`（既有工厂模式）。
- 版本契约：`features/v2.7.0/release-contract.md`（F045 只拥有 minimax 类型的 TTS 客户端注册；不新增错误码）。

## 3. 方案对比与选定

### 决策 1：新客户端 vs 改造 OpenAITTSClient

- **备选**：
  - A. 新增 `MiniMaxTTSClient`（独立文件，注册到 `_tts_client_type['minimax']`）— 与既有客户端模式一致；改动面小。
  - B. 给 OpenAITTSClient 加参数兼容两种协议 — 把 OpenAI SDK 与原生 JSON 协议塞进一个类，职责混乱；openai SDK 的 `audio.speech.create` 无法发 `voice_setting` 嵌套体，最终还是要绕开 SDK，等于重写。
- **选定**：A
- **原因**：协议请求体与响应编码都不同，且 OpenAITTSClient 依赖 openai SDK 的强类型 API，无法表达 MiniMax 原生字段。
- **何时该重新考虑**：若未来 aiping / MiniMax 官方同时提供标准 OpenAI TTS 协议，可废弃本客户端回退 openai 类型。

### 决策 2：注册到 minimax 类型 vs 新增服务商类型

- **备选**：
  - A. 复用 `minimax`（后端枚举 + 前端 provider 表单均已存在）。
  - B. 新增 `aiping` 类型 — 后端枚举、前端 provider、表单、i18n 全链路新增。
- **选定**：A（用户已确认）
- **原因**：aiping 的 TTS 底层就是 MiniMax 模型；minimax 类型的表单字段（`openai_api_base` + `openai_api_key`）恰好满足 base/key 需求。
- **何时该重新考虑**：aiping 出现非 MiniMax 的语音能力或需要独立计费展示时。

### 决策 3：端点路径（aiping `/audio/speech` vs 官方 `/t2a_v2`）

- **备选**：
  - A. 默认 `/audio/speech`，支持 `endpoint` 配置键覆盖（读服务商/模型合并后的 config）。
  - B. 硬编码单一路径。
- **选定**：A（用户已确认「主 aiping + 兼容官方」）
- **原因**：一次实现覆盖两个上游；`endpoint` 走既有 config 合并通道（`init_tts_client` 已把 server config 与 model config 合并进 params），不需要改前端表单。
- **何时该重新考虑**：官方 API 出现 auth 方式变化（当前两者都是 Bearer）。

### 决策 4：音频解码（base64 vs hex）

- **备选**：A. 按 provider 分叉；B. 自动探测。
- **选定**：B（实现细节：先确定性探测 hex —— 全字符 ∈ 0-9a-fA-F 且长度为偶数 → `bytes.fromhex`；否则严格 base64 `validate=True`。**不能**反过来用宽松 base64 兜底：hex 字符串全部落在 base64 字母表内，宽松解码会静默产出垃圾字节）
- **原因**：响应类型不可提前可靠预知，自动探测零配置；探测失败抛异常（AC-05）不会静默产出损坏数据。

### 决策 5：音色（voice）来源

- **备选**：A. 只用模型 config 的 `voice`；B. config `voice` + 调用参数覆盖。
- **选定**：B
- **原因**：与现有 TTS 客户端的 `synthesize(text, voice=None)` 签名对齐（OpenAITTSClient 同样支持运行时覆盖）；前端已强制 tts 模型必填 `voice`，写入 `model.config.voice`。
- **何时该重新考虑**：若引入音色库 UI，voice_id 枚举来源变化，不影响本客户端签名。

### 决策 6：前端 ASR/TTS 类型白名单拆分

- **备选**：
  - A. 现有 `showAsrTtsTypes` 直接加 `minimax`（ASR 选项也会出现在 minimax 下）。
  - B. 拆成 `showAsrTypes`（不含 minimax）与 `showTtsTypes`（含 minimax）。
- **选定**：B
- **原因**：后端只在 `_tts_client_type` 注册 minimax，不在 `_asr_client_type` 注册；A 会让用户在 minimax 下选 ASR 后在创建流程报错（体验差）。B 改动仍是一处组件内的局部变量。
- **何时该重新考虑**：minimax 出现 ASR 能力时把值加回 `showAsrTypes`。

## 4. 系统现状（接手必读）

### 4.1 数据流

**TTS 调用链（改动前）**：
`POST /llm/workbench/tts`（router.py:231）→ `LLMService.invoke_workbench_tts`（services/llm.py:1502）→ `get_bisheng_tts(model_id)` → `BishengTTS.get_bisheng_tts`（domain/llm/tts.py:72）→ `init_tts_client`（tts.py:95，合并 server+model config，按 `_tts_client_type[server_type]` 选 client 与 params_handler）→ 客户端 `synthesize(text, voice)` 返回 bytes → 存入 MinIO → 返回 URL。

**模型创建连通性测试（既有，不改）**：
`POST /llm`（api/router.py:150）→ `LLMService.add_llm_server`（services/llm.py:610）遍历 models，TTS 类型先实例化 `get_bisheng_tts(ignore_online=True)`，钩子 `test_model_status`（services/llm.py:722）再真实 `ainvoke("hello")` 合成一次 → 失败置 status=1。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| 服务商 config（minimax 类型） | `{"openai_api_base": str, "openai_api_key": str, "endpoint"?: str}` | base 需含协议与路径前缀（如 `https://aiping.cn/api/v1`）；`endpoint` 可选，默认 `/audio/speech`，可覆盖为 `/t2a_v2` | `MiniMaxTTSClient` |
| 模型 config（tts 类型） | `{"voice": "<voice_id>"}` | MiniMax 音色 id（如 `male-qn-qingse`）；前端 tts 表单必填 | `MiniMaxTTSClient` |
| 合成请求体 | `{"model", "text", "voice_setting": {"voice_id", "speed": 1.0, "vol": 1.0}, "audio_setting": {"sample_rate": 32000, "format": mp3, "channel": 1}}` | MiniMax 原生 T2A 风格；不携带 `stream` 字段（两上游默认非流式） | aiping `/audio/speech` / 官方 `/t2a_v2` |
| 合成响应 | `{"data": {"audio": "<base64 或 hex>"}}` | aiping=base64，官方=hex；自动探测解码 | `MiniMaxTTSClient` |
| 客户端返回 | `bytes`（mp3 等） | 与 `BaseTTSClient.synthesize` 契约一致 | 工作台 TTS 存储链路 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `bisheng/core/ai/tts/minimax_tts_client.py`（新增） | MiniMax 原生 T2A 协议合成：拼请求、解码响应、错误抛出 | 不碰 DB / 平台业务 |
| `bisheng/llm/domain/llm/tts.py`（改） | 注册 `_tts_client_type['minimax']` + `_get_minimax_params` | 不改既有三类型的处理 |
| `bisheng/core/ai/tts/__init__.py`（改） | 导出 `MiniMaxTTSClient` | — |
| `src/frontend/platform/src/pages/ModelPage/manage/ModelConfig.tsx`（改） | `showAsrTtsTypes` 拆为 `showAsrTypes` / `showTtsTypes`（后者含 minimax） | 不动表单/校验逻辑 |

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | aiping 网关 TTS 的 pydantic 校验要求 `text` 字段（OpenAI 风格的 `input` 422 拒绝）；顶层 `voice` 会被网关静默忽略并报 "empty field" | 照抄 OpenAI TTS 请求体必失败，且报错信息不指向原因 | `MiniMaxTTSClient` 只发 `text` + `voice_setting` |
| 2 | 同一请求体，aiping 返回 base64、MiniMax 官方返回 hex | 只按一种解码，切上游即产出损坏音频 | 解码自动探测（先 base64 后 hex） |
| 3 | minimax 服务商的 LLM（ChatMinimax）是 OpenAI 兼容协议，但它的 TTS **不是** —— 两者协议互不相关 | 误以为 "minimax 已 OpenAI 化" 而想复用 OpenAITTSClient | 本设计决策 1；`_tts_client_type['minimax']` 独立注册 |
| 4 | 前端 TTS/ASR 白名单 `showAsrTtsTypes` 原本含 `qianfan`，但后端 `_tts_client_type` / `_asr_client_type` 均未注册 qianfan（既有 UI/后端不一致） | 沿用该数组时误以为 UI 与后端一致 | 本次拆分为两个数组时**保持 qianfan 现状**（不在本特性范围修复） |
| 5 | `init_tts_client` 把 server config 与 model config 合并后交给 params_handler，且 model config 覆盖 server config | 在 model config 里塞 `endpoint` 能覆盖服务商级配置，排查时容易漏看 | params 合并是既有行为，设计上 `endpoint` 优先读合并结果 |

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `MiniMaxTTSClient`（`synthesize(text, voice=None, language=None, format="mp3") -> bytes`） | 内部 Python API（`bisheng.core.ai.tts`） | `BishengTTS` 工厂（llm/domain/llm/tts.py） |
| minimax 服务商可创建 `tts` 类型模型并可设为工作台默认 TTS | 既有 `/api/v1/llm` CRUD + `/llm/workbench` 配置 | platform 前端模型管理页 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| aiping 网关 TTS 协议（`/audio/speech`，MiniMax T2A 请求体，base64 响应） | 外部 HTTP API | 网关改协议/字段会静默坏掉合成；AC-05 会显式报错但需关注上游公告 |
| MiniMax 官方 T2A v2 协议（`/t2a_v2`，hex 响应） | 外部 HTTP API | 同上 |
| 既有 TTS 存储链路（MinIO）与 `invoke_workbench_tts` | 内部 | 本特性纯复用，不依赖其内部实现 |

## 7. 测试与可观测

- **单元测试**：`test/llm/test_minimax_tts_client.py`，用 `httpx.MockTransport` 覆盖：请求体形状（含 voice_setting/audio_setting）、base64 解码、hex 解码、非 200 抛异常、缺 audio 字段抛异常、voice 优先级（调用参数 > 配置）、endpoint 覆盖、默认 endpoint；另覆盖 `_get_minimax_params` 与 `_tts_client_type['minimax']` 映射（AST/导入断言，参考 `test_minimax_provider.py` 风格，避免 DB 依赖）。
- **手动验证**（真实环境）：
  1. 平台 API：`POST /api/v1/llm` 以 minimax 类型创建服务商（base=`https://aiping.cn/api/v1`，aiping key）+ tts 模型（`voice` 配置 `male-qn-qingse`），创建流程自带真实合成连通性测试。
  2. `POST /api/v1/llm/workbench` 设工作台 `tts_model` 后，`POST /api/v1/llm/workbench/tts {"text": "你好"}` 应返回 MinIO 音频 URL，可下载播放。
- **关键日志**：沿用 llm 模块 loguru 日志；合成失败时客户端异常带上游状态码与响应体。

## 8. 后续改进 / 不打算做的事

- 不做音色库管理页（voice_id 手填），待有真实需求再立 feature。
- 不做合成缓存 / 用量看板（复用既有 token 计费链路）。
- aiping 若升级为 OpenAI 兼容 TTS，本客户端可标记废弃（决策 1 触发条件）。

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-08-26 | 初版 | F045 设计定稿 |
| 2026-08-26 | 决策 4 补实现细节（hex 先探测 + 严格 base64）；实现完成，平台已配置 `AiPing-TTS`（模型 id=5）并设为工作台默认 TTS | 实现落地 + 实测验证 |
