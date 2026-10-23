# Design: 知识库知识图谱（GraphRAG 简化版）

> **本文档定位**：记录“在普通文档知识库中手动生成并浏览知识图谱”的方案定稿。
> 需求已明确收敛：**不自动生成、不接入检索、不做多跳问答**，只做“手动触发 → 抽取实体/关系/属性 → 可视化浏览”。
>
> **关联**：[schema.sql](./schema.sql)
> **版本**：v2.8.0（拟）
> **最后更新**：2026-10-20

---

## 1. 目标与非目标

### 1.1 目标

在普通文档知识库详情页增加「知识图谱」入口：用户手动点击后，后台对该知识库下所有解析成功的文件抽取实体、关系与属性，写入图数据表，并在前端以力导向图展示、搜索和追溯来源分块。

### 1.2 非目标（v1 明确不做）

- **不做自动生成**：解析成功后不触发图谱抽取，避免不必要的 LLM 开销。
- **不接入检索**：不参与 RAG、Agent、工作流、引用体系，不影响现有问答。
- **不做多跳推理问答**：不实现实体链接、子图召回、路径排序、RRF 融合。
- **不做社区摘要 / PageRank / 图算法**。
- **不做实体人工编辑、合并、删除**。
- **不支持问答知识库（QA）、个人知识库（PRIVATE）、知识空间（SPACE）**。
- **不做增量更新**：v1 重新生成即全量清空重建。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7（双 DB / 多租户 / 权限 / 分层 / 错误码等）。
- **不新增基础设施**：不引入 Neo4j、不新增 ES 索引、不新增 Milvus 集合、不新增 worker 进程。
- 图数据存 MySQL；抽取任务走 Celery；图谱可视化复用 platform 已有 ECharts。
- 只处理 `KnowledgeTypeEnum.NORMAL`（type=0）的普通文档知识库。
- 只处理 `KnowledgeFileStatus.SUCCESS`（status=2）的文件，并排除非主版本文件。
- 抽取输入来自 ES 中的 chunk（ES 是 chunk 的权威存储），不重新解析源文件。
- 抽取任务失败不能影响文件解析、检索和知识库本身的可用性。

---

## 3. 方案对比与选定

### 决策 1：手动按钮触发，而非解析后自动生成

- **备选**：
  - A. 解析成功后自动抽取：用户无感，但每个文件都产生 LLM 开销，且大库回填成本不可控。
  - B. 知识库详情页手动点击生成：按需触发，成本可控，符合本次需求。
- **选定**：B。
- **原因**：需求明确要求避免自动生成的性能开销；图谱是“按需生成的知识资产”，不是检索链路的必经环节。
- **何时重新考虑**：若未来要求图谱实时跟随文件变化，再评估增量更新 + 事件驱动。

### 决策 2：MySQL 存图，不引入 Neo4j

- **备选**：
  - A. MySQL 实体/关系/证据表：无新组件，事务、租户、备份复用；多跳查询弱。
  - B. Neo4j：图查询强，但新增服务、运维、备份、权限映射。
- **选定**：A。
- **原因**：v1 不做多跳检索，只做 Top-N 浏览和 1 跳邻居，MySQL 完全够用；避免给部署增加负担。
- **何时重新考虑**：单库边数达到百万级、需要 Cypher 式复杂关联查询、或需要图算法时。

### 决策 3：构建与解析解耦，独立 Celery 队列

- **备选**：
  - A. 解析 pipeline 中同步抽取：与解析强耦合，慢、易失败、拖垮文件处理。
  - B. 解析完成后另行投递图谱任务：失败隔离、可重试、可单独限流。
- **选定**：B。
- **原因**：图谱抽取是 LLM 密集型任务，耗时和失败率都高于解析；必须与文件解析解耦。
- **实现**：新增 `graph_celery` 队列，由现有 knowledge worker 同时消费，避免新增进程。

### 决策 4：图仅用于浏览，不接入检索

- **备选**：
  - A. 图召回 + 向量/BM25 RRF + 多跳迭代：价值高，但工作量和风险大。
  - B. 纯浏览：只做构建、查询、可视化。
- **选定**：B。
- **原因**：本次需求已明确去掉多跳推理与复杂关联查询；纯浏览方案不改 5 条检索链路和引用体系，工作量从 8–12 周降到 3–4 周。

### 决策 5：v1 重新生成采用全量清空重建

- **备选**：
  - A. 全量清空重建：实现简单、结果确定、不会残留脏数据。
  - B. 按文件增量：节省 token，但要处理实体计数、孤儿实体、删除/版本切换的一致性。
- **选定**：A。
- **原因**：手动触发本身已控制频率；v1 优先保证正确性和实现速度。
- **何时重新考虑**：大库（数千文件）重复生成成本过高时，再引入按文件 hash/更新时间的增量。

### 决策 6：v1 只支持普通文档知识库

- **备选**：
  - A. 所有知识库类型都支持：问答库的 chunk 结构和文档库不同，需要另一套输入映射和 Prompt。
  - B. 仅 `NORMAL`：复用现有文档 chunk，边界清晰。
- **选定**：B。
- **原因**：需求明确“仅普通文档知识库”；QA 库存的是问答对，不是文档分块，不能直接复用。
- **实现**：前端只在 `knowledge.type === 0` 时渲染入口；后端强校验，非 NORMAL 返回 `10997`。

### 决策 7：可视化使用 ECharts 而非 React Flow

- **备选**：
  - A. `@xyflow/react`：已是 platform 依赖，但需要自己实现力导向布局。
  - B. ECharts `graph` 系列：platform 已有全局 `echarts.min.js`，自带 force 布局、缩放、标签、点击事件。
- **选定**：B。
- **原因**：不新增依赖，快速得到可用的力导向图；React Flow 更适合后续做复杂编辑交互。
- **实现**：复用 `BaseChart.tsx` 的 `loadECharts()`。

### 决策 8：实体消歧只做确定性合并

- **备选**：
  - A. LLM 消歧：效果更好，但增加成本、延迟和不确定性。
  - B. 归一化 + 同名同类型合并：成本低、可预测。
- **选定**：B。
- **原因**：v1 图谱只用于浏览，不做检索；同名不同义的噪声可以接受，后续再引入 LLM 消歧。
- **合并键**：`(knowledge_id, normalized_name, entity_type)`。

---

## 4. 系统现状（接手必读）

### 4.1 现有链路

```text
上传 → 解析 → 分块 → Embedding → Milvus（向量）+ ES（BM25 / chunk 权威存储）
```

关键位置：

| 环节 | 位置 |
|---|---|
| 文件解析任务 | `worker/knowledge/file_worker.py:295` `parse_knowledge_file_celery` |
| 分块与向量化 | `api/services/knowledge_imp.py:106` `process_file_task`、`:204` `addEmbedding` |
| 文件处理 pipeline | `knowledge/rag/knowledge_file_pipeline.py` |
| ES 向量库初始化 | `knowledge/domain/knowledge_rag.py` `init_knowledge_es_vectorstore_sync` |
| 文件删除 | `api/services/knowledge_imp.py:135` `delete_vector_files` |
| 知识库删除 | `knowledge/domain/services/knowledge_service.py:892` `delete_knowledge_file_in_vector` |
| 权限校验 | `KnowledgeService._get_writable_knowledge` / `_get_readable_knowledge` |
| 租户过滤 | `core/database/tenant_filter.py` `_TENANT_AWARE_MODEL_MODULES` |

### 4.2 关键数据结构

| 结构 | 位置 | 说明 |
|---|---|---|
| `Knowledge` / `knowledge` 表 | `knowledge/domain/models/knowledge.py:148` | 知识库主表；`type` 区分 NORMAL/QA/PRIVATE/SPACE |
| `KnowledgeFile` / `knowledgefile` 表 | `knowledge/domain/models/knowledge_file.py:190` | 文件表；`status=2` 表示解析成功 |
| ES chunk | `knowledge.index_name` 或 `collection_name` | `_source.text` 为包装后的 chunk；`metadata.document_id` = `knowledgefile.id` |
| chunk 解包 | `knowledge/domain/services/knowledge_utils.py:47` `split_chunk_metadata` | 从 `<paragraph_content>` 中取出正文 |
| 非主版本文件 | `knowledge/domain/services/knowledge_flow_retrieval.py:41` `_fetch_non_primary_file_ids` | 图构建也应跳过非主版本，避免重复 |
| 权限 | `KnowledgeService.permission_service.ensure_knowledge_write_async` / `ensure_knowledge_read_async` | 生成用写权限，查看用读权限 |

### 4.3 关键模块职责

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `knowledge_graph_service.py` | 构建编排、查询、清空、过期标记、级联 | 不直接拼 LLM prompt |
| `graph_worker.py` | Celery 任务、ES 读取、LLM 调用、进度更新 | 不处理 HTTP |
| `knowledge_graph.py`（endpoints） | 鉴权、参数校验、响应封装 | 不写业务逻辑 |
| `knowledge_graph.py`（models） | 4 张表定义 | 不含业务规则 |
| `KnowledgeGraph/*`（前端） | 入口、状态机、画布、详情 | 不直接拼后端 SQL |

---

## 5. 详细设计

### 5.1 数据模型

完整可执行 SQL 见 [schema.sql](./schema.sql)。本节只列字段职责。

#### 5.1.1 `knowledge` 表新增字段

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `graph_state` | SmallInteger | 0 | 0 未生成 / 1 生成中 / 2 已生成 / 3 生成失败 / 4 已过期 |
| `graph_built_at` | DateTime | NULL | 最近一次成功生成完成时间 |
| `graph_job_id` | BigInteger | NULL | 最近一次生成任务 ID |

#### 5.1.2 `knowledge_graph_job`

一次生成任务。记录模型、实体类型、进度、token 消耗、错误信息。

#### 5.1.3 `knowledge_graph_entity`

实体表。唯一键 `(knowledge_id, normalized_name, entity_type)`；`attributes` 存属性 JSON；`aliases` 存别名；`mention_count` 用于 Top-N 排序。

#### 5.1.4 `knowledge_graph_relation`

关系表。唯一键 `(knowledge_id, source_entity_id, target_entity_id, rel_type)`；`weight` 为 0–1 置信度；`attributes` 存关系属性。

#### 5.1.5 `knowledge_graph_evidence`

证据表。`entity_id` / `relation_id` 二选一；`document_id + chunk_index + chunk_pk` 指向 ES 原始 chunk，用于来源追溯和文件删除时的精确清理。

> **不建外键**：与现有表一致，级联由服务层显式处理，避免 DM8/软删除场景下的约束问题。

#### 5.1.6 Alembic 与手动 SQL 的衔接

当前阶段由 DBA/开发者手动执行 `schema.sql`。后续编码时新增的 Alembic 迁移 `f091_knowledge_graph` 必须：

- 使用 `table_exists` / `column_exists` / `index_exists` 守卫；
- 保证在“已手动建表”的库上执行 `alembic upgrade head` 不报错；
- 或者在迁移文件落地后执行 `alembic stamp f091_knowledge_graph`，把版本表对齐。

### 5.2 生成流程

```text
POST build
  → 写权限校验
  → KB type == NORMAL 校验
  → Redis 锁 kg:build:{knowledge_id}
  → force 校验（已有图谱必须 force=true）
  → 创建 job，knowledge.graph_state=1
  → 投递 build_knowledge_graph_celery
      → 枚举文件（SUCCESS + 非主版本）
      → 每个文件统计 chunk 数
      → 批量派发 extract_knowledge_graph_file_celery
          → ES 按 document_id 读 chunk
          → split_chunk_metadata 解包
          → LLM 抽取
          → JSON 校验 / 重试
          → 实体归一化 / 合并
          → 写 entity / relation / evidence
          → 更新 job 进度
      → 收尾：清理孤儿、重算 mention_count、更新 graph_state
```

#### 文件枚举

```python
KnowledgeFile.knowledge_id == knowledge_id
KnowledgeFile.file_type == FileType.FILE.value
KnowledgeFile.status == KnowledgeFileStatus.SUCCESS.value
# 再排除非主版本文件
```

#### ES 读取

```python
es_client = KnowledgeRag.init_knowledge_es_vectorstore_sync(knowledge=knowledge)
index_name = knowledge.index_name or knowledge.collection_name
query = {"query": {"term": {"metadata.document_id": file_id}}}
```

- 每页 500；大文件用 `search_after` 或 `scroll`；
- `text = KnowledgeUtils.split_chunk_metadata(source["text"])`；
- `chunk_index = source["metadata"]["chunk_index"]`；
- `chunk_pk = hit["_id"]`。

#### 写入策略

- 单文件任务开始前：删除该 `document_id` 的旧 evidence，保证幂等；
- 实体 upsert：按唯一键查询，存在则更新描述/属性/别名/计数，不存在则插入；
- 唯一键冲突：`IntegrityError` 后回查并更新；
- 关系 upsert：`source/target` 用本次 chunk 的实体名称解析，端点不存在则丢弃该关系；
- 单文件一个事务，避免半成品。

### 5.3 抽取 Prompt

新增 `core/prompts/yaml/knowledge_graph.yaml`，使用现有 `chat_prompt` 格式与 `${var}` 模板。

```yaml
version: v1
description: 知识图谱实体与关系抽取
prompts:
  extract_prompt:
    description: "从单个文档分块中抽取实体、关系与属性"
    type: chat_prompt
    prompt:
      system: |
        你是企业知识图谱抽取器。请从给定文本中抽取实体、实体之间的关系以及实体属性。

        规则：
        1. 只抽取文本中明确出现或可直接推断的事实，禁止编造。
        2. 实体类型必须从用户给出的类型列表中选择；无法归类时使用“其他”。
        3. 关系必须由文本直接支持，source 和 target 必须是本次输出的实体名称。
        4. 属性只保留文本中明确出现的键值，无法确定则省略。
        5. 每个分块最多输出 15 个实体、20 条关系。
        6. 只输出 JSON，不要输出 Markdown 代码块、解释或额外文字。

        输出格式：
        {
          "entities": [
            {
              "name": "实体名称",
              "type": "实体类型",
              "description": "基于文本的一句话描述",
              "attributes": {"属性名": "属性值"}
            }
          ],
          "relations": [
            {
              "source": "源实体名称",
              "target": "目标实体名称",
              "type": "关系类型",
              "description": "关系描述",
              "weight": 0.8,
              "attributes": {}
            }
          ]
        }
      user: |
        实体类型列表：${entity_types}
        文件名：${file_name}
        文本：
        <text>
        ${chunk_text}
        </text>
```

校验与容错：

1. 去掉 ```json 代码块；`json.loads` 失败用 `json_repair`。
2. 顶层必须是对象，`entities` / `relations` 必须是数组。
3. 丢弃空名称、超长名称（>255 char）、未知类型（归为“其他”）、端点不在本次实体列表中的关系。
4. `weight` 截断到 `[0, 1]`，默认 0.7。
5. JSON 解析失败重试 2 次，追加错误信息；仍失败则记录该 chunk 失败，继续后续。
6. 单 chunk LLM 超时 120 秒。

归一化：

```python
def normalize_entity_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    name = name.lower()
    name = re.sub(r"\s+", "", name)
    name = name.strip("，。；：、,.!?()（）[]【】\"'“”‘’")
    return name[:255]
```

### 5.4 Celery 任务

| 任务 | 作用 |
|---|---|
| `build_knowledge_graph_celery(knowledge_id, job_id)` | 协调：清旧图、枚举文件、统计 chunk、派发文件任务 |
| `extract_knowledge_graph_file_celery(knowledge_id, job_id, file_id)` | 单文件抽取、写库、更新进度 |
| `reconcile_knowledge_graph_job_celery()` | Beat 每 5 分钟扫描异常 `RUNNING` 任务，重派或标失败 |

队列与注册：

```python
# settings.py CeleryConf
"bisheng.worker.knowledge.graph.*": {"queue": "graph_celery"}
```

```python
# worker/__init__.py
from bisheng.worker.knowledge.graph_worker import (
    build_knowledge_graph_celery,
    extract_knowledge_graph_file_celery,
    reconcile_knowledge_graph_job_celery,
)
```

Worker 启动：

```bash
celery -A bisheng.worker.main worker \
  -Q knowledge_celery,graph_celery \
  --concurrency=2 \
  --loglevel=info
```

进度：

```sql
UPDATE knowledge_graph_job
SET done_files = done_files + 1,
    done_chunks = done_chunks + :n,
    token_in = token_in + :tin,
    token_out = token_out + :tout
WHERE id = :job_id AND tenant_id = :tenant_id;
```

收尾：

1. 删除零证据实体/关系；
2. 按 evidence 重算 `mention_count`；
3. job 状态：全成功=2，部分失败=3，全失败=4；
4. `knowledge.graph_state=2/3`，`graph_built_at=now()`；
5. 释放 Redis 锁。

取消与续跑：

- 取消：job `status=5`，文件任务在 chunk 边界检查并退出；
- 续跑：以文件为粒度，`acks_late=True` + 幂等重跑；
- reconcile：超时未完成的 `RUNNING` 任务重新派发未完成文件，或标记失败。

### 5.5 过期与级联

| 事件 | 处理 |
|---|---|
| 新增 / 重解析 / 编辑 chunk | `mark_stale(knowledge_id)` → `graph_state=4` |
| 删除文件 | 删除该 `document_id` 的 evidence；清理零证据实体/关系；`mark_stale` |
| 删除知识库 | 删除该 KB 的 job/entity/relation/evidence |
| 重新生成 | `force=true` → 清空旧图 → 新建 job |
| 清空图谱 | 删除图数据，`graph_state=0`，保留 job 历史 |

兜底：`GET status` 时比较 `graph_built_at` 与 `MAX(knowledgefile.update_time)`，发现文件更新则返回 `stale=true`。

### 5.6 API

统一前缀 `/api/v1/knowledge`。新增 `knowledge/api/endpoints/knowledge_graph.py`，在 `bisheng/api/router.py` 注册。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/{knowledge_id}/graph/build` | 写 | 生成/重新生成 |
| GET | `/{knowledge_id}/graph/status` | 读 | 状态 + 进度 |
| POST | `/{knowledge_id}/graph/cancel` | 写 | 取消 |
| GET | `/{knowledge_id}/graph/overview` | 读 | 实体数/关系数/时间/过期 |
| GET | `/{knowledge_id}/graph/graph` | 读 | Top-N 节点与边 |
| GET | `/{knowledge_id}/graph/entities` | 读 | 实体搜索/分页 |
| GET | `/{knowledge_id}/graph/entities/{entity_id}` | 读 | 实体详情 + 关系 + 证据 |
| GET | `/{knowledge_id}/graph/entities/{entity_id}/neighbors` | 读 | 1 跳邻居 |
| POST | `/graph/public/neighbors` | **无（匿名）** | 按知识库 ID 查全图（≤500 节点）或按实体名称查完整 1 跳子图，对外接口 |
| DELETE | `/{knowledge_id}/graph` | 写 | 清空图谱 |

#### 公开邻居查询（匿名，POST `/api/v1/knowledge/graph/public/neighbors`）

> 对客版接口文档（可直接提供给外部对接方）：[`docs/api/knowledge-graph-public-api.md`](../../api/knowledge-graph-public-api.md)

外部系统通常只知道实体名称，不知道内部实体 ID，因此单独提供按名称解析的匿名接口；只传知识库 ID 时返回全库图谱（最多 500 个节点）。

请求体（`entity_name` 可选）：

```json
{
  "knowledge_id": 148,
  "entity_name": "张伟"
}
```

两种模式，由 `entity_name` 是否有值决定：

| 入参 | 返回 |
|---|---|
| 传 `entity_name` | 该实体的完整 1 跳子图；不截断；查不到名称返回空图 |
| 不传 / 传空串 | 知识库完整图谱；最多 500 个节点（按 `mention_count` 取 Top），节点间的全部关系边；超出时 `truncated=true` |

行为约定：

- `entity_name` 先按 `knowledge_graph_entity.name` 精确匹配；未命中再按 `normalize_entity_name()` 归一化后精确匹配 `normalized_name`（仅消除全半角/大小写/空白/标点差异，不做模糊匹配）。
- 同名实体可能存在于多个 `entity_type`，取 `mention_count` 最高者，`id` 升序兜底。
- **查不到实体不报错**：返回 `data: {nodes: [], edges: [], truncated: false}`，不再返回 10999。
- **按实体名称查询不截断**：与登录态 `/neighbors`（默认 100、上限 300）不同，返回全部关系边，供外部系统完整消费。
- **全图模式**：节点数超过 500 时截断到 Top 500，`truncated=true` 提示调用方；边不做额外截断（只返回保留节点之间的关系）。
- 入参错误（`knowledge_id < 1`、`entity_name` 超 255）由 FastAPI 返回 422。
- 无 JWT，因此服务层在 `bypass_tenant_filter()` 内查询；`knowledge_id` 即访问凭据。

> **安全影响**：匿名接口意味着任何知道 `knowledge_id` 的调用方都能读取该库图谱。若部署在公网，应在网关层增加来源白名单、鉴权或限流；如需长期对外提供，建议改为 API Key（`/api/v2` 体系）而不是永久匿名。

#### 生成请求

```json
{
  "model_id": 12,
  "entity_types": ["人物", "组织", "地点", "事件", "产品", "概念"],
  "force": true
}
```

#### 图数据响应

```json
{
  "nodes": [
    {
      "id": 1,
      "name": "张伟",
      "type": "人物",
      "description": "某公司技术总监",
      "attributes": {"职位": "技术总监"},
      "mention_count": 8,
      "degree": 12
    }
  ],
  "edges": [
    {
      "id": 10,
      "source": 1,
      "target": 2,
      "label": "任职于",
      "weight": 0.9,
      "description": "张伟担任某公司技术总监"
    }
  ],
  "truncated": true
}
```

#### 实体详情响应

```json
{
  "entity": {
    "id": 1,
    "name": "张伟",
    "type": "人物",
    "description": "...",
    "attributes": {"职位": "技术总监"},
    "mention_count": 8
  },
  "relations": [
    {
      "id": 10,
      "direction": "out",
      "label": "任职于",
      "other_entity": {"id": 2, "name": "某公司", "type": "组织"},
      "weight": 0.9
    }
  ],
  "evidence": [
    {
      "document_id": 55,
      "document_name": "员工手册.pdf",
      "chunk_index": 12,
      "snippet": "张伟担任某公司技术总监……"
    }
  ]
}
```

#### 错误码（`common/errcode/knowledge.py`）

| Code | 类 | 消息 |
|---|---|---|
| 10992 | `KnowledgeGraphJobRunningError` | 知识图谱正在生成中，请稍后再试 |
| 10993 | `KnowledgeGraphNoFilesError` | 知识库中没有解析成功的文件 |
| 10994 | `KnowledgeGraphModelNotConfiguredError` | 未配置知识图谱抽取模型 |
| 10995 | `KnowledgeGraphNotReadyError` | 知识图谱尚未生成 |
| 10996 | `KnowledgeGraphBuildFailedError` | 知识图谱生成失败: {exception} |
| 10997 | `KnowledgeGraphTypeNotSupportedError` | 仅普通文档知识库支持生成知识图谱 |
| 10998 | `KnowledgeGraphAlreadyExistsError` | 知识图谱已存在，请确认重新生成 |
| 10999 | `KnowledgeGraphEntityNotFoundError` | 实体不存在 |

### 5.7 模型配置

在 `KnowledgeLLMConfig`（`llm/domain/schemas.py:127`）新增：

```python
graph_model_id: int | None = Field(None, description="知识图谱抽取模型ID")
graph_entity_types: list[str] | None = Field(
    default=None,
    description="知识图谱默认实体类型；为空时使用内置默认值",
)
```

默认实体类型：

```python
DEFAULT_GRAPH_ENTITY_TYPES = ["人物", "组织", "地点", "事件", "产品", "概念"]
```

- 未配置 `graph_model_id` 时回退 `extract_title_model_id`；仍为空返回 10994。
- 前端配置入口：`platform/src/pages/ModelPage/manage/tabs/KnowledgeModel.tsx`。
- 调用时使用新增的 `ApplicationTypeEnum.KNOWLEDGE_GRAPH`，让 token 成本独立统计。

```python
LLMService.get_bisheng_llm_sync(
    model_id=model_id,
    app_id=ApplicationTypeEnum.KNOWLEDGE_GRAPH.value,
    app_name=ApplicationTypeEnum.KNOWLEDGE_GRAPH.value,
    app_type=ApplicationTypeEnum.KNOWLEDGE_GRAPH,
    user_id=create_user_id,
)
```

### 5.8 前端

#### 入口

在 `KnowledgePage/components/Files.tsx` 操作栏、`HitTestDialog` 旁增加「知识图谱」按钮：

```tsx
<KnowledgeGraphButton
    knowledgeId={id}
    canEditKb={canEditKb}
    knowledgeType={knowledgeType}
/>
```

- 仅 `knowledgeType === 0`（NORMAL）渲染；
- 读权限可查看；写权限可生成/重新生成/清空。

#### 状态机

| 状态 | 界面 |
|---|---|
| 未生成 | 空状态 + 「生成知识图谱」 |
| 生成中 | 进度条 + 文件数/分块数/失败数 + 「取消」 |
| 已生成 | 画布 + 搜索 + 类型筛选 + 「重新生成」「清空」 |
| 失败 | 失败原因 + 「重新生成」 |
| 已过期 | 黄色提示条 + 「重新生成」 |

#### 组件拆分

```text
platform/src/pages/KnowledgePage/components/KnowledgeGraph/
├── KnowledgeGraphButton.tsx
├── KnowledgeGraphDrawer.tsx
├── BuildGraphDialog.tsx
├── GraphProgress.tsx
├── GraphCanvas.tsx
├── GraphToolbar.tsx
├── EntityDetailPanel.tsx
└── GraphEmptyState.tsx
```

#### ECharts 配置

复用 platform 全局 `echarts.min.js`（`BaseChart.tsx` 的 `loadECharts()`），不新增 npm 依赖。

核心配置：

```ts
{
  series: [{
    type: 'graph',
    layout: 'force',
    roam: true,
    draggable: true,
    force: { repulsion: 200, edgeLength: [80, 160], gravity: 0.1 },
    categories: entityTypes.map(t => ({ name: t })),
    data: nodes.map(n => ({
      id: String(n.id),
      name: n.name,
      category: typeIndex[n.type],
      symbolSize: Math.min(12 + n.mention_count * 2, 60),
      value: n.mention_count,
    })),
    edges: edges.map(e => ({
      source: String(e.source),
      target: String(e.target),
      label: { show: false, formatter: e.label },
      lineStyle: { width: 1 + e.weight },
    })),
    label: { show: true, position: 'right', fontSize: 11 },
    emphasis: { focus: 'adjacency' },
  }]
}
```

交互：

- 点击节点 → 加载实体详情；
- 双击节点 → 以该节点为中心加载 1 跳邻居；
- 类型筛选 → 重新请求图数据；
- 搜索实体 → 选中后加载邻居视图；
- 超过 500 节点只展示 Top N，并提示通过搜索定位。

#### 国际化

在 `platform/public/locales/{zh-Hans,en-US,ja}/knowledge.json` 增加 `knowledgeGraph.*` 文案，三套同步。

---

## 6. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | ES 中的 `text` 是 `<file_title>/<file_abstract>/<paragraph_content>` 包装后的内容 | 把标题/摘要喂给 LLM，实体抽取污染 | `KnowledgeUtils.split_chunk_metadata` |
| 2 | ES 元数据里的 `document_id` 是 `knowledgefile.id`，不是 `knowledge.id` | 证据、删除、进度全部对不上 | 统一使用 `knowledgefile.id` |
| 3 | 非主版本文件也存在于 ES 中 | 同一文档的多个版本被重复抽取，实体计数虚高 | 复用 `_fetch_non_primary_file_ids` |
| 4 | 租户过滤只覆盖 SELECT，批量 UPDATE/DELETE 不过滤 | 跨租户删除图数据 | 所有批量语句手写 `tenant_id` |
| 5 | `knowledge.index_name` 和 `collection_name` 可能不同 | 读错 ES 索引 / 读不到 chunk | 优先 `index_name`，回退 `collection_name` |
| 6 | 知识库解析任务有 fair scheduler 和 per-file 锁 | 图谱任务误用解析锁会互相阻塞 | 图谱使用独立 `kg:file:*` 锁和 `graph_celery` |
| 7 | QA 库的 chunk 结构与文档库不同 | 对 QA 库抽取会得到无意义结果 | 后端强校验只支持 NORMAL |
| 8 | platform 的 ECharts 是全局脚本，不是 npm 包 | 直接 `import echarts` 会失败 | 复用 `loadECharts()` |
| 9 | MySQL JSON 列不能有默认值 | 迁移报错 | JSON 列一律 NULL |
| 10 | 手动执行 SQL 后 Alembic 版本表不会自动前进 | 下次 `alembic upgrade` 可能重复建表 | 迁移必须用存在性守卫，或 `alembic stamp` |
| 11 | SQLModel `Session.exec()` 必须配合 `sqlmodel.select`；误用 `sqlalchemy.select` 会返回 `Row` | `job.id`/`job.status` 报 `KeyError`/`AttributeError`，状态接口 500、收尾失败、任务卡死 | 图数据相关查询统一 `from sqlmodel import select` |
| 12 | MySQL 中文件 `status=SUCCESS` 不代表 ES 索引仍存在（ES 被重置/删索引） | 每个文件任务读 ES 404，图谱为空 | 构建前检查 `knowledge.index_name` 是否存在，缺失时直接失败并提示重建索引 |
| 13 | 进度只在“整个文件完成”时写回 job | 单个文件几十个 chunk 时前端进度条长时间为 0，看起来像卡死 | 每个 chunk 完成后更新 `done_chunks`/token，进度条优先按 chunk 计算 |

---

## 7. 对外契约与依赖

### 7.1 提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `/api/v1/knowledge/{id}/graph/*` | HTTP API | platform 前端 |
| `KnowledgeGraphService.mark_stale(knowledge_id)` | 内部 Python API | 文件解析、删除、chunk 编辑、版本切换 |
| `knowledge_graph_job/entity/relation/evidence` | DB 表 | 运维、后续检索扩展 |

### 7.2 依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| ES chunk 存储结构 | `_source.text` + `metadata.document_id/chunk_index` | ES 索引重建/字段变更会影响抽取 |
| `LLMService` / `BishengLLM` | 内部 Python API | 模型配置缺失、限流、超时 |
| `KnowledgeFile.status` / 版本链 | DB 表 | 状态或版本规则变更会影响文件枚举 |
| 租户过滤器 | `tenant_filter.py` | 新模型未注册会失去租户隔离 |
| ECharts 全局脚本 | 前端运行时 | 脚本加载失败时画布不可用 |

---

## 8. 测试与可观测

### 8.1 单元测试

- Prompt JSON 解析：正常、代码块包裹、非法 JSON、缺字段、未知类型、关系端点缺失。
- 名称归一化与合并：全半角、空白、大小写、标点。
- 文件枚举：排除 QA 库、失败文件、非主版本。
- 进度更新与收尾：全成功、部分失败、全失败、取消。
- 过期标记：新增/删除/编辑文件后状态正确。
- 错误码：权限、类型、并发锁、无文件、模型未配置。

### 8.2 集成测试

- 普通文档 KB → 生成 → 状态 → 图数据 → 实体详情。
- QA 库调用生成 → 10997。
- 无写权限调用生成 → 403。
- 并发两次生成 → 第二次 10992。
- 删除文件 → evidence 清理、图谱标记过期。
- 租户隔离：A 租户不能读取 B 租户图数据。
- worker 中断 → reconcile 标记失败或续跑。

### 8.3 前端手工验收

- 未生成 → 生成 → 进度 → 完成 → 画布渲染。
- 搜索、类型筛选、点击节点、双击邻居、来源分块。
- 重新生成、清空、取消。
- 小图（<50 节点）和大图（>500 节点）表现。
- 三套语言文案齐全。

### 8.4 抽取质量抽样

选取 20–50 个代表性 chunk，人工核对：

- 实体边界（人名、机构名、产品名）；
- 关系方向与类型；
- 属性是否来自原文；
- 是否出现明显重复实体。

### 8.5 可观测

- 日志：`knowledge_graph` trace，记录 job_id、file_id、chunk_index、token usage、失败原因。
- 指标：生成文件数、失败文件数、token 消耗、平均 chunk 耗时。
- 前端：进度、失败文件数、token 消耗可见。

---

## 9. 实施任务拆分

| 编号 | 任务 | 预估 |
|---|---|---|
| P0-1 | 表结构 + Alembic 迁移 + 租户注册 | 1 天 |
| P0-2 | 错误码 + 枚举 + 配置字段 + Prompt 文件 | 0.5 天 |
| P0-3 | API Schema 与路由骨架 | 0.5 天 |
| P1-1 | 任务创建、锁、文件枚举、进度模型 | 1.5 天 |
| P1-2 | ES 读取 + LLM 抽取 + JSON 校验/重试 | 2 天 |
| P1-3 | 实体合并、关系写入、evidence、收尾清理 | 1.5 天 |
| P1-4 | 取消、断点续跑、reconcile、级联/过期 | 1.5 天 |
| P2-1 | 查询类 API（状态/概览/图/实体/详情/邻居） | 1.5 天 |
| P2-2 | 前端入口 + 状态机 + 生成弹窗 + 进度 | 2 天 |
| P2-3 | 实体搜索 + 详情面板 | 1 天 |
| P3-1 | ECharts 画布 + 交互 + 大图降采样 | 2 天 |
| P3-2 | i18n、单测、集成测试、部署文档 | 2 天 |
| **合计** | | **约 3–4 周**（1 人全栈） |

---

## 10. 部署与回退

### 10.1 部署步骤

```bash
# 1. 手动执行 schema.sql（当前阶段）
mysql -u<user> -p<password> <db_name> < docs/features/knowledge_graph/schema.sql

# 2. 后续 Alembic 迁移落地后
cd src/backend
alembic upgrade head

# 3. 重启后端

# 4. worker 增加 graph_celery 队列
celery -A bisheng.worker.main worker \
  -Q knowledge_celery,graph_celery \
  --concurrency=2 \
  --loglevel=info

# 5. 系统模型设置中配置图谱抽取模型与默认实体类型
```

### 10.2 回退步骤

1. 前端隐藏「知识图谱」入口（发布回滚）；
2. 后端下线路由，不影响现有知识库与检索；
3. 如需彻底回退：

```sql
DROP TABLE IF EXISTS knowledge_graph_evidence;
DROP TABLE IF EXISTS knowledge_graph_relation;
DROP TABLE IF EXISTS knowledge_graph_entity;
DROP TABLE IF EXISTS knowledge_graph_job;
ALTER TABLE knowledge
  DROP COLUMN graph_job_id,
  DROP COLUMN graph_built_at,
  DROP COLUMN graph_state;
```

4. 图数据独立存储，回退不影响 Milvus/ES 中的原有 chunk。

---

## 11. 验收清单

- [ ] 仅普通文档知识库显示「知识图谱」入口；
- [ ] 点击生成后 30 秒内进入“生成中”，进度持续更新；
- [ ] 生成完成后可看到实体/关系，画布可缩放、搜索、筛选、点击看详情；
- [ ] 实体详情能看到属性与来源分块；
- [ ] 同一知识库不能并发生成；
- [ ] 非 `can_edit` 用户看不到生成/重新生成/清空；
- [ ] 文件变更后显示“图谱可能过期”；
- [ ] 删除文件后对应证据被清理；
- [ ] QA 知识库调用接口返回 10997；
- [ ] 租户隔离生效；
- [ ] 取消任务后状态正确；
- [ ] 三套语言文案齐全。

---

## 12. 后续改进 / 不打算做的事

- **增量更新**：按文件 `update_time` / hash 只重抽变更文件。
- **LLM 实体消歧**：合并同名不同义、简称、别名。
- **图检索**：实体链接 + 子图召回 + RRF，接入 RAG。
- **多跳问答**：路径排序 + 迭代检索。
- **社区摘要**：Louvain/Leiden + LLM 摘要。
- **图谱导出**：JSON/CSV/GraphML。
- **人工编辑**：合并、删除、修改实体和关系。

以上均不在 v1 范围；若需求升级，再按新的 spec 评估。

---

## 13. 实现落点（2026-10-20）

| 层 | 文件 |
|---|---|
| 数据模型 | `bisheng/knowledge/domain/models/knowledge_graph.py` |
| 表结构 SQL | `docs/features/knowledge_graph/schema.sql` |
| Alembic | `bisheng/core/database/alembic/versions/v2_8_0_f091_knowledge_graph.py` |
| 抽取规则 | `bisheng/knowledge/domain/services/knowledge_graph_extraction.py` |
| 编排/查询 | `bisheng/knowledge/domain/services/knowledge_graph_service.py` |
| Celery | `bisheng/worker/knowledge/graph_worker.py` |
| API | `bisheng/knowledge/api/endpoints/knowledge_graph.py` |
| Prompt | `bisheng/core/prompts/yaml/knowledge_graph.yaml` |
| 前端 API | `src/frontend/platform/src/controllers/API/knowledgeGraph.ts` |
| 前端组件 | `src/frontend/platform/src/pages/KnowledgePage/components/KnowledgeGraph/` |
| 模型配置 | `src/frontend/platform/src/pages/ModelPage/manage/tabs/KnowledgeModel.tsx` |
| 单测 | `test/knowledge/test_knowledge_graph_extraction.py` |

**已验证**：后端语法编译、ruff、11 个图谱单测、`alembic heads`（`f091_knowledge_graph`）、前端 `tsc` / eslint / `pnpm build`、i18n 三语一致性。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-10-20 | 初版定稿 | 需求收敛为“手动生成 + 可视化浏览” |
