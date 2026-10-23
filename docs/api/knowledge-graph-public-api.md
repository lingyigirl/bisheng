# 知识图谱公开查询接口

> 面向：**外部系统对接方**
> 接口地址：`POST {BASE_URL}/api/v1/knowledge/graph/public/neighbors`
> 鉴权：**无需登录、无需 Token、无需 API Key**（匿名接口）
> 数据格式：`application/json`
> 在线调试：若环境开放了平台后端 `/docs`，可在其中查看并试用本接口
> 最后更新：2026-10-23

**`{BASE_URL}` 说明**：由平台方提供的环境地址（例如 `https://ai.example.com`，不要带末尾 `/`），请替换后再调用。

---

## 1. 接口说明

根据知识库 ID 查询知识图谱，支持两种模式：

| 模式 | 请求是否传 `entity_name` | 返回内容 |
|---|---|---|
| **实体模式** | 传实体名称 | 该实体的 **1 跳邻居子图**：中心实体 + 直接相连的实体 + 它们之间的关系边。**不截断**，全量返回 |
| **全图模式** | 不传 / 传 `null` / 传空串 | 该知识库的完整图谱。节点数超过 **500** 时，按实体提及次数取前 500 个节点，并返回 `truncated: true` |

补充规则：

- 实体模式按**实体名称精确匹配**（先匹配原始名称，再匹配归一化名称，消除全半角、大小写、空白、标点差异，不做模糊搜索）。
- 同名实体可能存在于多个实体类型中，接口取 `mention_count` 最高的那个；相同时取 ID 最小的。
- **实体名称查不到时不会报错**，返回空图（`nodes: []`、`edges: []`）。
- 知识库不存在、图谱尚未生成或生成失败，同样返回空图。
- 全图模式的 500 是**节点上限**，不是边上限：返回保留节点之间的全部关系边。
- 本接口只读，不会修改任何数据。

---

## 2. 请求

### 2.1 请求头

| Header | 必填 | 说明 |
|---|---|---|
| `Content-Type` | 是 | 固定为 `application/json` |
| `Authorization` | 否 | **不需要**。接口数据范围只由 `knowledge_id` 决定，与登录用户无关 |

### 2.2 请求体

| 参数 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| `knowledge_id` | integer | 是 | `>= 1` | 知识库 ID |
| `entity_name` | string | 否 | 长度 `<= 255` | 实体名称。不传、传 `null`、空串或仅空白字符时返回知识库完整图谱 |

```json
{
  "knowledge_id": 148,
  "entity_name": "张伟"
}
```

### 2.3 请求示例

**查询指定实体：**

```bash
curl -X POST "{BASE_URL}/api/v1/knowledge/graph/public/neighbors" \
  -H "Content-Type: application/json" \
  -d '{"knowledge_id": 148, "entity_name": "张伟"}'
```

**查询知识库完整图谱：**

```bash
curl -X POST "{BASE_URL}/api/v1/knowledge/graph/public/neighbors" \
  -H "Content-Type: application/json" \
  -d '{"knowledge_id": 148}'
```

**Python（requests）：**

```python
import requests

resp = requests.post(
    "{BASE_URL}/api/v1/knowledge/graph/public/neighbors",
    json={"knowledge_id": 148, "entity_name": "张伟"},
    timeout=30,
)
body = resp.json()
if body["status_code"] == 200:
    nodes = body["data"]["nodes"]
    edges = body["data"]["edges"]
```

---

## 3. 响应

### 3.1 统一响应信封

```json
{
  "status_code": 200,
  "status_message": "SUCCESS",
  "data": {
    "nodes": [],
    "edges": [],
    "truncated": false
  }
}
```

**重要：HTTP 状态码通常恒为 `200`，业务结果以响应体的 `status_code` 为准。** 请勿仅凭 HTTP 200 判断成功，务必检查 `status_code == 200`。

### 3.2 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `status_code` | integer | 业务状态码，`200` 表示成功；`422` 参数校验失败；`500` 服务端异常 |
| `status_message` | string / array | 成功时为 `SUCCESS`；失败时为错误说明或参数校验明细 |
| `data` | object | 图谱数据；失败时通常不含该字段 |
| `data.nodes` | array | 实体节点列表 |
| `data.edges` | array | 关系边列表 |
| `data.truncated` | boolean | 是否因节点超过 500 而截断。实体模式恒为 `false` |

### 3.3 节点对象（`data.nodes[]`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 实体 ID（图谱内部 ID） |
| `name` | string | 实体名称（原文） |
| `type` | string | 实体类型，如 `人物`、`组织`、`地点`、`事件`、`产品`、`概念`、`其他`。取决于建图时的配置与抽取结果 |
| `description` | string \| null | 实体描述 |
| `attributes` | object \| null | 实体属性键值对，如 `{"职位": "技术总监"}` |
| `mention_count` | integer | 实体在文档中的提及次数，可用于衡量重要度 |
| `degree` | integer | **本次返回子图内**与该节点相连的边数，不是全图度数 |

### 3.4 关系边对象（`data.edges[]`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 关系 ID |
| `source` | integer | 源实体 ID，对应某个 `nodes[].id` |
| `target` | integer | 目标实体 ID，对应某个 `nodes[].id` |
| `label` | string | 关系类型/标签，如 `任职于`。取值来自抽取结果，不保证是固定枚举 |
| `weight` | number | 关系置信度，范围 `0 ~ 1`，默认 `1.0` |
| `description` | string \| null | 关系描述 |

> 边的方向固定为 `source → target`，不随查询视角变化（即使查询的是 `target` 实体的 1 跳邻居）。

---

## 4. 响应示例

### 4.1 实体模式：命中

请求：

```json
{"knowledge_id": 148, "entity_name": "张伟"}
```

响应：

```json
{
  "status_code": 200,
  "status_message": "SUCCESS",
  "data": {
    "nodes": [
      {
        "id": 470,
        "name": "张伟",
        "type": "人物",
        "description": "某公司技术总监",
        "attributes": {"职位": "技术总监"},
        "mention_count": 8,
        "degree": 1
      },
      {
        "id": 12,
        "name": "某公司",
        "type": "组织",
        "description": "一家科技公司",
        "attributes": null,
        "mention_count": 15,
        "degree": 1
      }
    ],
    "edges": [
      {
        "id": 10,
        "source": 470,
        "target": 12,
        "label": "任职于",
        "weight": 0.9,
        "description": "张伟担任某公司技术总监"
      }
    ],
    "truncated": false
  }
}
```

### 4.2 实体模式：名称不存在

```json
{
  "status_code": 200,
  "status_message": "SUCCESS",
  "data": {
    "nodes": [],
    "edges": [],
    "truncated": false
  }
}
```

### 4.3 全图模式：未超过 500 个节点

请求：

```json
{"knowledge_id": 148}
```

响应结构同上，`nodes` / `edges` 为全库数据，`truncated` 为 `false`。

### 4.4 全图模式：超过 500 个节点

`nodes` 只返回按 `mention_count` 降序排列的前 500 个节点，`edges` 返回这些节点之间的全部关系，`truncated` 为 `true`：

```json
{
  "status_code": 200,
  "status_message": "SUCCESS",
  "data": {
    "nodes": ["... 共 500 个 ..."],
    "edges": ["... 保留节点之间的全部关系 ..."],
    "truncated": true
  }
}
```

> 需要完整数据时，可按实体名称逐个查询其 1 跳子图，或联系平台方导出图谱。

---

## 5. 错误与异常

| HTTP 状态 | `status_code` | 场景 | 处理建议 |
|---|---|---|---|
| `200` | `200` | 成功（含实体不存在、图谱为空） | 正常解析 `data` |
| `200` | `422` | `knowledge_id` 缺失/非整数/`< 1`；`entity_name` 非字符串或超过 255 字符 | 按 `status_message` 中的字段明细修正请求 |
| `200` | `500` | 服务端异常 | 可重试；持续失败请联系平台方 |
| `404` | — | 请求路径错误（框架默认返回 `{"detail":"Not Found"}`） | 检查 URL 是否与本文一致 |

参数校验失败响应示例：

```json
{
  "status_code": 422,
  "status_message": [
    {
      "type": "string_too_long",
      "loc": ["body", "entity_name"],
      "msg": "String should have at most 255 characters",
      "input": "...",
      "ctx": {"max_length": 255}
    }
  ]
}
```

---

## 6. 使用注意事项

1. **接口是匿名接口**，请勿将地址公开到公网前端。建议在网关层增加来源 IP 白名单、访问频率限制或网络隔离。
2. `knowledge_id` 是平台知识库 ID，可从平台知识库详情页 URL 或平台方提供的清单中获取。
3. 实体名称建议直接使用图谱返回的 `nodes[].name`，匹配成功率最高；传入其他写法时，接口会尝试归一化精确匹配（全半角、大小写、空白、标点差异）。
4. 图谱是**手动触发生成**的知识资产。如果知识库尚未生成图谱，或生成失败，本接口返回空图；文件变更后图谱可能过期，本接口不返回过期标记。
5. 实体模式不截断。对于超级节点（关系极多），响应体可能较大，请设置合理的超时与内存上限。
6. 本接口为只读接口，可安全重试；同一知识库的图谱数据在两次生成之间是稳定的，可按 `knowledge_id` / `entity_name` 做客户端缓存。
7. 响应中的 `type`、`label`、`attributes` 来自文档抽取结果，不同知识库之间不保证一致，请按字符串处理，不要依赖固定枚举。

---

## 7. 自检清单

- [ ] 请求方法为 `POST`，路径为 `/api/v1/knowledge/graph/public/neighbors`
- [ ] `Content-Type: application/json`
- [ ] 请求体包含 `knowledge_id`，且为正整数
- [ ] 判断成功时检查响应体 `status_code == 200`，而不是只看 HTTP 状态码
- [ ] 实体不存在时按空图处理，不要当成调用失败
- [ ] 全图模式下检查 `data.truncated`，为 `true` 时数据不完整
