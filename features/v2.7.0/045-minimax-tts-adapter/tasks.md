# Tasks: F045 MiniMax TTS 适配

**关联规格**: [spec.md](./spec.md) · [design.md](./design.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-08-26 用户确认 |
| design.md | ✅ 已评审 | 2026-08-26 用户确认 |
| tasks.md | ✅ 已拆解 | 2026-08-26 sdd-review tasks LGTM |
| 实现 | ✅ 完成 | 6 / 6 完成（2026-08-26）。偏差处理见 design.md 顶部调整原则 |

---

## 开发模式

按 Wave 组织：无依赖任务并行，依赖链串行。设计论证指向 design §3/§4/§5，不在此复制。

---

## Tasks

### Wave 1（并行）

- [x] **T001**: MiniMaxTTSClient 单元测试（Test-First）
  **文件**: `src/backend/test/llm/test_minimax_tts_client.py`
  **逻辑**: 用 `httpx.MockTransport` 编写测试，覆盖：请求体形状（model/text/voice_setting.voice_id/audio_setting）、base64 解码、hex 解码、非 200 抛异常（含响应信息）、缺 data.audio 抛异常、voice 优先级（调用参数 > 配置）、endpoint 覆盖、默认 endpoint=/audio/speech、无 voice 配置快速失败
  **覆盖 AC**: AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-10
  **依赖**: 无

- [x] **T004**: 前端模型类型白名单拆分
  **文件**: `src/frontend/platform/src/pages/ModelPage/manage/ModelConfig.tsx`
  **逻辑**: `showAsrTtsTypes = ['azure_openai', 'openai', 'qwen', 'qianfan']` 拆为 `showAsrTypes`（同原值）与 `showTtsTypes`（原值 + `minimax`）；模板中 ASR 选项用 showAsrTypes、TTS 选项用 showTtsTypes。qianfan 现状保持不动（design §5 坑 4）
  **覆盖 AC**: AC-08, AC-09
  **验证**: `pnpm typecheck` + 模型管理页手动检查（minimax 服务商下出现 TTS、无 ASR 选项）
  **依赖**: 无

### Wave 2

- [x] **T002**: MiniMaxTTSClient 实现
  **文件**: `src/backend/bisheng/core/ai/tts/minimax_tts_client.py`
  **逻辑**: 按 design §4.2 契约实现：POST `{base_url}{endpoint}`，MiniMax 原生请求体（text/voice_setting/audio_setting），响应 `data.audio` base64→hex 自动探测解码，非 200/缺字段/解码失败抛带响应信息的异常，voice 优先级调用参数 > 配置，缺 voice 快速失败；支持可选 `http_client`（openai_proxy 复用）
  **测试**: T001 全部通过
  **覆盖 AC**: AC-02, AC-03, AC-04, AC-05, AC-06, AC-07
  **依赖**: T001

### Wave 3

- [x] **T003**: minimax 类型 TTS 注册
  **文件**: `src/backend/bisheng/llm/domain/llm/tts.py`, `src/backend/bisheng/core/ai/tts/__init__.py`（另在 `src/backend/test/llm/test_minimax_tts_client.py` 追加映射断言）
  **逻辑**: `_tts_client_type` 增加 `LLMServerType.MINIMAX.value: {"client": MiniMaxTTSClient, "params_handler": _get_minimax_params}`；`_get_minimax_params` 按 design §4.2 从合并 params 取 api_key/model/base_url/endpoint/voice/openai_proxy；`__init__.py` 导出 MiniMaxTTSClient；映射断言测试（AST/导入，参考 test_minimax_provider.py 风格，不触 DB）
  **覆盖 AC**: AC-01, AC-09
  **依赖**: T002

### Wave 4

- [x] **T005**: 平台配置 aiping TTS 模型
  **逻辑**: 后端重启加载新代码后，API 创建 minimax 类型服务商（name=`AiPing-TTS`，config: openai_api_base=https://aiping.cn/api/v1 + aiping key）+ tts 模型（model_name=MiniMax-Speech-2.8-hd，config voice=`male-qn-qingse`）；创建流程自带真实合成连通性测试（status=0 即通过）
  **覆盖 AC**: AC-01
  **依赖**: T003（后端重启）

### Wave 5

- [x] **T006**: 工作台默认 TTS + 端到端验证
  **逻辑**: `POST /api/v1/llm/workbench` 设 tts_model 为新模型；`POST /api/v1/llm/workbench/tts {"text": "你好"}` 返回 MinIO 音频 URL，下载验证为有效 mp3 可播放
  **覆盖 AC**: AC-11
  **依赖**: T005

---

## 实际偏差记录

| 日期 | 偏差 | 处理 |
|------|------|------|
| 2026-08-26 | T002 解码顺序比 design 决策 4 更严谨：先确定性探测 hex（全 hex 字符且偶数长），再严格 base64（`validate=True`）。理由：hex 字符串同样是合法 base64 字母表，宽松 base64 解码会静默产出垃圾字节 | 已回写 design.md 决策 4 |
| 2026-08-26 | T005 验证时发现本机 `test/llm` 套件有 17 个**预存在**失败（`RuntimeError: Event loop is closed`，与 F045 无关，经 stash 对照验证）；F045 新增测试 15/15 通过 | 记录，不在本特性修复 |
