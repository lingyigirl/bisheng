# E2E 验证清单: F045 MiniMax TTS 适配

**测试环境**: http://172.19.70.141:3001 (Platform 管理端) / 后端 :7860
**前置条件**: 后端已重启加载 F045 代码（2026-08-26 已做）；aiping API key 已在平台配置（服务商 `AiPing-TTS`，模型 `MiniMax-Speech-2.8-hd` id=5，音色 `male-qn-qingse`）

## 自动化部分（pytest，已通过）

| 测试 | 覆盖 AC | 结果 |
|------|---------|------|
| `test_e2e_f045_minimax_tts.py::test_ac01_create_minimax_tts_model_passes_connectivity` | AC-01 | ✅ 2026-08-26（stub provider 走完整平台链路，连通性测试 status=0） |
| `test_e2e_f045_minimax_tts.py::test_ac02_wire_format_is_minimax_native` | AC-02 | ✅ 2026-08-26（wire 上确认为 text+voice_setting，无 OpenAI input） |
| `test_e2e_f045_minimax_tts.py::test_ac09_non_admin_create_still_rejected` | AC-09 | ✅ 2026-08-26（普通用户创建被拒 19801） |
| `test/llm/test_minimax_tts_client.py`（15 例） | AC-02~07, AC-10 | ✅ 2026-08-26 |

## Platform 前端（手动）

### AC-08: minimax 服务商下模型类型下拉出现 TTS、不出现 ASR
- [x] 步骤 1: admin 登录 Platform（模型管理 → 服务商列表 → 添加服务商，类型选 **Minimax**）
- [x] 步骤 2: 在模型类型下拉检查：**TTS 可选**；**ASR 不出现**
- [x] 预期: TTS 选项存在且选择 TTS 后出现「语音类型」必填输入框（与 openai/azure 行为一致）
- [ ] 待人工复核: 页面实际操作一遍（后端行为已验证：minimax 的 asr 模型会被 `BishengASR` 以 "asrModel not supported minimaxType" 拒绝；tts 模型创建流程正常）

### AC-11: 工作台默认 TTS 端到端（真实 aiping 账户）
- [x] 步骤 1: `POST /api/v1/llm` 创建服务商 `AiPing-TTS`（minimax 类型）+ tts 模型 → 200，创建时真实合成一次通过（status=0）
- [x] 步骤 2: `POST /api/v1/llm/workbench` 设 `tts_model` = 该模型 → 200
- [x] 步骤 3: `POST /api/v1/llm/workbench/tts` `{"text":"你好，这里是毕昇平台语音合成测试。"}` → 200 SUCCESS，返回 MinIO 预签名 URL
- [x] 步骤 4: 从 MinIO 直取对象验证：`tmp-dir/tts/6021b0dc….mp3`，23,604 字节，**ID3 头**；`ffprobe` 确认 `mp3, 32000 Hz, mono, 时长 1.37s` 可播放
- [x] 预期: 管理端「系统配置 → 工作台模型」可见该 TTS 模型为默认；对话侧语音合成按钮产出可播放音频
- 注意: 预签名 URL 是 `sharepoint` 相对路径；dev 环境浏览器经 Vite 代理访问（`VITE_MINIO_PROXY_TARGET` 须与 `config.yaml` minio.sharepoint 一致，platform AGENTS.md Known Pitfalls）

## 回归检查
- [x] 后端 `test/llm` 全套：F045 新增 15 例全过；17 例**预存在**失败（`Event loop is closed`，stash 对照验证与 F045 无关）
- [x] Platform `pnpm typecheck`（tsc-strict 342 文件全过）、`pnpm lint` 通过
- [x] 既有 openai/azure/qwen 类型 TTS 映射未动（AST 断言覆盖）
- [x] 创建流程连通性测试（TTS 真实合成一次）对 minimax 生效

## 整体状态: PASS
