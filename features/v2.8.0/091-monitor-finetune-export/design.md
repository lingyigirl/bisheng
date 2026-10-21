# Design: 智能体监测导出微调数据集

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.8.0
**最后更新**: 2026-10-19

---

## 1. 目标与约束

- **目标**：把智能体监测筛选命中的运行，按「运行 → chat_id + 时间窗 → 当轮问题/答案」关联，生成微调指令集 JSON。
- **只读**：不新增表、不新增列、不改既有写入行为（release-contract 表 1 登记为非 Owner）。数据来源为既有 ES 遥测索引 + `chatmessage`。
- **不改同事已推送代码**：复用 F052 既有查询构造与权限判定；前端只改监测页与其 API 模块。
- **多租户**：ES 沿用 `_tenant_user_ids` 用户集合；`chatmessage` 查询显式 `tenant_id == 当前租户`。
- **无新增依赖**：后端仅用标准库 + 既有 SQLModel/ES 客户端；前端不做新库引入。

---

## 2. 数据流

```
前端「导出」按钮
  └─ GET /api/v1/telemetry/agent-transactions/export
       ├─ 权限：非管理员需审计日志菜单（复用 _check_log_permission）
       ├─ 时间窗：AgentTransactionExportQuery.resolve_window（默认 7 天 / 上限 90 天）
       ├─ ES：application_process 事件，同列表筛选，collapse(trace_id)
       │       size = limit + 1，超过 5000 → HTTP 400
       ├─ 提取每条运行：trace_id / chat_id / start_time / end_time
       ├─ MySQL：chatmessage where chat_id in (...) and tenant_id = ... and
       │          category in (question + 各应用答案类) and
       │          create_time ∈ [min(start)-300s, max(end)+300s]
       ├─ 逐运行配对：当轮问题 + 当轮最终答案（见 §4）
       ├─ 反馈过滤 + 文本解包 + 去重
       └─ JSON 数组 → application/json 附件下载
```

**为什么问答不在 ES 里取**：`application_process` 事件只有 `chat_id` 与 ≤200 字 `input_snapshot`，完整答案只存在于 `chatmessage`；按 `chat_id` + 运行时间窗关联是唯一可靠来源。

---

## 3. 数据来源与字段

### 3.1 ES `base_telemetry_events`（运行维度）

| 字段 | 用途 |
|------|------|
| `trace_id` | 运行唯一标识，折叠维度 |
| `event_data.application_process_chat_id` | 关联 `chatmessage.chat_id` |
| `event_data.application_process_start_time` / `end_time` | epoch 秒，用于时间窗配对 |
| `user_context.user_id` | 租户用户集合过滤 |
| `timestamp` | 排序（desc，collapse 取最新一条） |

### 3.2 MySQL `chatmessage`（问答维度）

| 字段 | 用途 |
|------|------|
| `chat_id` | 关联运行 |
| `category` | 区分问题与答案类型 |
| `message` | 原始文本 / JSON 信封 |
| `liked` | 反馈：0 未评价 / 1 点赞 / 2 差评 |
| `create_time` | 运行时间窗配对 |
| `tenant_id` | 租户隔离 |

**问题类**：`question`（各应用统一）。
**答案类**：`answer`（助手）、`stream_msg` / `output_msg` / `output_with_input_msg` / `output_with_choose_msg`（工作流）、`task`（linsight）、`agent_answer`（工作台）。
**故意排除**：`input`（向用户追问的提示）、`agent_thinking` / `agent_tool_call` / `reasoning_answer`（推理与工具过程，不是训练目标）。

---

## 4. 问答配对规则

对每条运行 `[start, end]`：

1. **问题**：在 `[start - 300s, end]` 内取**最新**一条 `question`；若没有，回退到 `end` 之前最近的一条 `question`。
   - 问题一定先于答案写入，因此**不接受 `end` 之后的问题**，避免把下一轮问题配给本轮答案。
2. **答案**：在 `[start, end + 60s]` 内取**最新**一条答案类记录。
   - 若窗口内出现 `end` 之后的 `question`（下一轮已开始），只接受该问题之前的答案，避免短流程下一轮答案落进 slack 被误配。
   - `stream` 分片不落库（`ChatHistory.add_message` 只保存 `type != 'stream'`），所以每条答案记录都是完整文本。
   - 工作流多个输出节点会产生多条答案记录，取最后一条作为最终答案（与评测执行侧"取输出事件"的口径一致）。
3. 问题或答案缺失 → 跳过该运行。
4. 同一 `chat_id` 的长时间会话用时间窗隔离；300 秒前向 slack 覆盖"问题先落库"，60 秒后向 slack 覆盖"答案先于进程事件落库"。

---

## 5. 文本解包

两端存储的都是 JSON 信封，`message` 列通常不是纯文本：

- **问题**：`{"query": "...", "files": [...]}`（工作台）、`{"data": {...}, "input": "..."}`（应用对话）、纯文本。
  → 依次尝试 `query`、`input`，失败则原样返回。
- **答案**：`{"content": "..."}`（知识库/QA）、`{"msg": "..."}`（智能体 / 工作流）、`{"events": [...]}`（部分新格式只把文本放在 `events[].type == "text"`）。
  → 依次尝试 `content`、`msg`、`events` 中的 text 分片；thinking / tool_call 丢弃。

> 与 `ConversationExportService` 的 `_extract_query_text` / `_extract_answer_text` 归一化口径一致。本 feature **不 import 该类**：其模块在导入期拉起 pypandoc、知识空间等服务，遥测模块不应为此耦合；两段纯函数体量很小，本地实现并在注释中标注同源。

---

## 6. 反馈过滤

| 模式 | 保留条件 |
|------|---------|
| `exclude_disliked`（默认） | `liked != 2` |
| `liked` | `liked == 1` |
| `all` | 全部 |

反馈取**答案行**的 `liked`（问题行不承载反馈）。未评价（0）在默认模式下保留，因为线上真实流量大部分未评价，排除差评已足够避免把明确负面样本喂回训练。

---

## 7. API

### `GET /api/v1/telemetry/agent-transactions/export`

**入参（query）**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `app_id` | string | 否 | — | 应用 ID |
| `app_type` | string | 否 | — | workflow / assistant / workstation / linsight |
| `status` | string | 否 | — | success / failed |
| `keyword` | string | 否 | — | 输入快照 / trace_id |
| `start_time` | int | 否 | 最近 7 天 | epoch 秒 |
| `end_time` | int | 否 | 当前时间 | epoch 秒 |
| `feedback` | string | 否 | `exclude_disliked` | all / liked / exclude_disliked |
| `limit` | int | 否 | 5000 | 1–5000，硬上限 |

**成功响应**：`200 application/json; charset=utf-8`

```json
[
  {"instruction": "本月流水分析", "input": "", "output": "本月支出……"}
]
```

Header：`Content-Disposition: attachment; filename="agent_finetune_<start>_<end>.json"`。

**错误**

| 场景 | 状态码 | detail |
|------|--------|--------|
| 时间范围 > 90 天 | 400 | `time range exceeds 90 days` |
| `start_time >= end_time` | 400 | `start_time must be before end_time` |
| 命中运行 > limit | 400 | `export exceeds 5000 runs; narrow the time range` |
| 无日志菜单权限 | 401 | 未授权 |

> 沿用遥测/评测模块既有做法：直接 `HTTPException` + detail，不新分配 MMM 错误码。

---

## 8. 后端实现清单

| 文件 | 变更 |
|------|------|
| `telemetry_search/domain/schemas/agent_transactions.py` | 新增 `ExportFeedback`、`AgentTransactionExportQuery`（feedback/limit 校验） |
| `telemetry_search/domain/services/agent_transactions.py` | 新增文本解包、配对、反馈过滤、样本构建、chatmessage 批量读取、导出 ES 查询、`export_finetune_dataset` |
| `telemetry_search/api/endpoints/agent_transactions.py` | 新增 `GET /export`，返回附件下载 |

关键函数：

- `_map_export_trace(hit)`：运行 → `{trace_id, chat_id, start, end}`；缺 `chat_id` 的运行不可导出。
- `_load_chat_rows(chat_ids, start, end, tenant_id)`：按窗口取 Q&A 类消息，`IN` 列表按 500 分块。
- `_pick_qa(rows, start, end)`：§4 配对规则。
- `build_finetune_samples(traces, rows_by_chat, feedback)`：过滤、解包、去重、组装。
- `_query_export_transactions(query, tenant_id)`：`size = limit + 1` 检测超限。
- `AgentTransactionsService.export_finetune_dataset(...)`：串起权限 → 查询 → 配对 → JSON 字节。

---

## 9. 前端实现清单

| 文件 | 变更 |
|------|------|
| `controllers/API/log.ts` | 新增 `exportAgentTransactionsApi`：`responseType: 'blob'`、`silent: true`；新增 `ExportFeedback` 类型 |
| `pages/LogPage/agentMonitor/index.tsx` | 反馈筛选下拉、导出按钮、`bsConfirm` 敏感数据确认、Blob 下载、Blob 错误体解析 |
| `public/locales/{zh-Hans,en-US,ja}/bs.json` | 新增导出与反馈筛选文案（三语同 PR） |

**错误处理**：`responseType: 'blob'` 会把后端 JSON 错误体也包成 Blob，全局拦截器只能显示 HTTP 状态码。因此 API 走 `silent`，页面读取 Blob 文本并取 `detail`，用 toast 展示超限等可操作提示。

---

## 10. 决策记录

| # | 决策 | 备选 | 原因 |
|---|------|------|------|
| 1 | 导出入口放在智能体监测筛选栏 | 新增独立页面 / 放到微调模块 | 需求明确"监测菜单下智能体监测"，数据与筛选都在此页 |
| 2 | 全量导出 + 5000 硬上限 | 只导当前页 / 静默截断 | 微调需要完整数据集；静默截断会让用户误以为数据完整 |
| 3 | 单轮样本 | 带 history 多轮 | 多轮配对与格式校验复杂，MVP 先满足"格式可上传"；后续可增量扩展 |
| 4 | 默认排除差评 | 默认全部 / 默认仅点赞 | 差评是唯一不能回流训练的负信号；未评价样本仍有价值 |
| 5 | 反馈取答案行 `liked` | 读 `message_feedback_event` | `liked` 是当前页面与导出共同使用的主口径，事件表为追加记录；本期不引入第二口径 |
| 6 | 文本解包本地实现 | import `ConversationExportService` | 避免遥测模块拉起文档导出依赖（pypandoc 等）；函数小且口径已对齐 |
| 7 | 不做审计落库 | 新增 `EventType` + 三语枚举 | 涉及前后端枚举与 i18n 联动，超出本次导出功能范围；先以服务日志记录操作人 |
| 8 | 不脱敏 | 导出前脱敏 | 银行脱敏规则未确认；先以确认框提示，规则确定后单独排期 |

---

## 11. 风险与坑

1. **时区**：ES `start_time/end_time` 是 epoch 秒；`chatmessage.create_time` 是数据库本地时间的 naive datetime。配对统一用进程本地时间 `datetime.timestamp()` / `fromtimestamp()`；要求应用进程 TZ 与数据库一致（部署默认如此）。
2. **下一轮误配**：问题严格限制在 `end` 之前；答案除 60 秒后向 slack 外，还会在下一轮问题出现后截止，避免把下一轮答案算进来。
3. **工作流多输出节点**：取时间窗内最后一条答案；若业务要求拼接所有输出，可在 `build_finetune_samples` 扩展（当前与评测口径一致）。
4. **超长会话**：`chatmessage` 查询按全局时间窗 + `IN` 分块（500），避免一次拉取整段会话或触发占位符上限。
5. **ES collapse 计数**：`size = limit + 1` 用返回条数判断超限，不依赖 `hits.total`（collapse 下 total 语义易误解）。
6. **无法关联的运行**：缺 `chat_id`、缺时间、或问答不完整时跳过；导出条数可能少于运行数，属预期。
7. **空导出**：返回 `[]`，前端仍下载空数组文件，便于用户确认"确实没有可导出数据"。

---

## 12. 微调链路研究结论（需求："研究下那块微调的使用看看"）

### 12.1 平台内微调流程

1. **上传训练文件**：`POST /api/v1/finetune/job/file` → 存入 MinIO `finetune/train_file/personal/`（`FinetuneFileService.upload_file`）。
2. **创建训练任务**：`POST /api/v1/finetune/job`，请求体 `train_data` 携带文件引用；`FinetuneService.parse_command_train_file` 把 MinIO 文件转成共享链接，拼进 `params.dataset`。
3. **提交外部训练服务**：`SFTBackend.create_job` 调用 `POST {host}/v2.1/models/sft_elem/infer`，`options=['train']`，由 SFT-Backend 下载数据集并训练。
4. **训练完成后发布**：`/job/publish` 把训练产物发布为可部署模型。

### 12.2 格式与校验

- 平台侧**不解析训练文件内容**：普通上传只落 MinIO；唯一内容校验在预置训练集编辑 `FinetuneFileService.save_preset_file_content`（`finetune_file.py:119`），要求 JSON 数组且每项含 `instruction`、`output`。
- 训练服务模板（`sample.json`）为 Alpaca 风格：`instruction`（必填）、`output`（必填）、`input`（选填）、`history`（选填，`[[轮次指令, 轮次回答], ...]`）。
- `history` 是否生效取决于 SFT-Backend，本仓库代码不读取该字段。

### 12.3 对导出的结论

- 本次导出的单轮 `{instruction, input, output}` 与模板必填项完全一致，**可直接上传训练，无需补 `history`**。
- 单轮 vs 多轮的取舍见 §10 决策 3：当前数据以单轮任务型调用为主，且历史轮次没有反馈质量信号，硬拼 `history` 会引入噪声。
- **风险提示**：普通上传没有服务端内容校验，格式错误只会在训练阶段暴露。导出侧必须保证字段与模板一致（当前已由 T02 单测覆盖）。
