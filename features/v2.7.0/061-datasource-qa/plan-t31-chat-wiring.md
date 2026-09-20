# F061 T-31 对话侧接线 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已经写好但全仓无人调用的数据源对话能力接进助手链路——管理员能绑定数据源、模型能查到它、回答能带上可展开的引用锚点。

**Architecture:** 三处改动，各自独立可测。① 绑定写入侧补 `AssistantLink.datasource_id` 的 DAO / 请求 / 响应；② 在 `AssistantAgent.init_tools` 这个「所有助手入口的唯一交汇点」注入 `DatasourceQueryTool`；③ 扩 `AssistantCitationToolWrapper` 加数据源分支，把 `QueryOutcome` 转成 `DatasourceCitationPayloadSchema` 并放进既有的 citation registry 链路。

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / LangChain `BaseTool` / pytest（`asyncio_mode=auto`）

**设计依据:** `design.md` 决策 15（含两个实现坑与一条锚点豁免）。本计划不重复论证，只给可执行步骤。

---

## 开工前必读

- 测试放 `src/backend/test/datasource/`，与既有同名文件并列；**不要**放 `test/` 根目录。
- 跑测试：`cd src/backend && uv run python -m pytest test/datasource/<file>.py -q`
- **不要**对目录跑 `ruff format`（会波及他人已提交文件）；只对自己改的文件跑。
- 参考写法：`test/test_tool.py` 与 `test_datasource_permission.py` 是同一模块的既有测试，风格照抄——DB 层可以伪造（I/O 边界），**被测逻辑本身不许 mock 掉**。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/backend/bisheng/database/models/assistant.py` | `AssistantLink` 的列 + `AssistantLinkDao` 增删方法 | 改（`:376+` 加一个 classmethod） |
| `src/backend/bisheng/api/v1/schemas.py` | `AssistantUpdateReq` / `AssistantInfo` 的字段 | 改（各加一个字段） |
| `src/backend/bisheng/api/services/assistant.py` | 保存时的写入分支 + 详情页的读取分桶 | 改（两处） |
| `src/backend/bisheng/datasource/domain/langchain/tool.py` | `DatasourceQueryTool.last_outcome` 改累积 | 改（`:73` 附近） |
| `src/backend/bisheng/datasource/domain/services/conversation_service.py` | `build_tools_for_assistant` 的 login_user 契约 | 改（文档 + 构造约束） |
| `src/backend/bisheng/api/services/assistant_agent.py` | 工具注入 + 锚点桥接 | 改 |
| `src/backend/bisheng/citation/domain/services/citation_prompt_helper.py` | `select_registry_items_for_persistence` 豁免数据源 | 改（`:282-294`） |
| `src/backend/test/datasource/test_assistant_binding.py` | 绑定写入/读取 + DAO 回归 | 新建 |
| `src/backend/test/datasource/test_tool_outcomes.py` | `last_outcome` 累积 | 新建 |
| `src/backend/test/datasource/test_assistant_anchor.py` | 锚点桥接 + 豁免 | 新建 |

---

## Task 0: 落地前先把 login_user 的契约钉死（**先做，否则 Task 3 会返工**）

**背景**：`build_tools_for_assistant(assistant_links, login_user, ...)` 需要一个 **`UserPayload`**（带可调用的 `get_visible_tenants`），但调用点 `AssistantAgent` 只有 `invoke_user_id: int`。传 `None` 或裸 `LoginUser` 会**静默跳过租户关卡**（`permission_service.py:1018-1020`：`login_user is None or not callable(get_visible_tenants)` → 直接 `return False, None`），也就是跨租户可见。既有先例：`ToolExecutor._build_permission_user`（`tool/domain/services/executor.py:95`）正是为此构造 `UserPayload(user_id=..., user_name="", user_role=[])`，且 `assistant_agent.py:275` 的 `skip_unauthorized=True` 已经依赖它。

**Files:**
- Modify: `src/backend/bisheng/datasource/domain/services/conversation_service.py`

- [ ] **Step 1: 在 `build_tools_for_assistant` 的 docstring 里写明契约**

在现有 docstring 的 `"""Tools for the datasources an assistant is linked to, minus unauthorized.` 段落后追加：

```python
    `login_user` MUST be a `UserPayload` (callable `get_visible_tenants`), not
    a bare `LoginUser` and not `None`. `PermissionService.check` skips its
    tenant gate for both of those (`permission_service.py:1018-1020`), so a
    caller that passes one silently drops tenant isolation instead of failing.
    Build one the way `ToolExecutor._build_permission_user` does.
```

- [ ] **Step 2: 加一条断言，把「静默」变成「响亮」**

在同文件 `build_tools_for_assistant` 函数体最前面（`wanted = [...]` 之前）插入：

```python
    if login_user is None or not callable(getattr(login_user, 'get_visible_tenants', None)):
        raise ValueError(
            'build_tools_for_assistant requires a UserPayload with get_visible_tenants; '
            'passing None or a bare LoginUser would skip the tenant gate silently'
        )
```

- [ ] **Step 3: 跑既有测试确认没打破**

Run: `cd src/backend && uv run python -m pytest test/datasource/ -q`
Expected: PASS（既有测试若传的是 MagicMock，`getattr(..., 'get_visible_tenants', None)` 会返回一个 Mock —— 它 callable，断言通过）

- [ ] **Step 4: 提交**

```bash
git add src/backend/bisheng/datasource/domain/services/conversation_service.py
git commit -m "fix(datasource): F061 — make the login_user contract on build_tools_for_assistant explicit

PermissionService.check skips its tenant gate whenever login_user is None
or lacks a callable get_visible_tenants, so a caller passing either would
drop tenant isolation without any error. Assert instead of documenting."
```

---

## Task 1: `AssistantLinkDao.update_assistant_datasource`

**Files:**
- Modify: `src/backend/bisheng/database/models/assistant.py:376+`（`AssistantLinkDao` 末尾）
- Test: `src/backend/test/datasource/test_assistant_binding.py`（新建）

- [ ] **Step 1: 写会失败的测试**

新建 `src/backend/test/datasource/test_assistant_binding.py`：

```python
"""F061 T-31: binding a datasource to an assistant, end to end through the DAO.

The trap this file exists for: `datasource_id` is the only one of the four
link columns whose default is NULL, and SQL's `NULL != 0` evaluates to NULL
rather than TRUE. A delete filter copied from `update_assistant_tool` would
therefore match nothing, and every save would append another duplicate row
instead of replacing. `test_saving_twice_does_not_duplicate` is that guard.
"""

from unittest.mock import MagicMock, patch

from bisheng.database.models.assistant import AssistantLink, AssistantLinkDao


def _session_capturing(added):
    """A session whose delete-call and added rows we can inspect afterwards."""
    session = MagicMock()
    session.query.return_value.filter.return_value.delete.return_value = 0
    session.add.side_effect = added.append
    return session


def _ctx(session):
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = False
    return ctx


def test_saving_twice_does_not_duplicate():
    added = []
    session = _session_capturing(added)

    with patch(
        'bisheng.database.models.assistant.get_sync_db_session',
        return_value=_ctx(session),
    ):
        AssistantLinkDao.update_assistant_datasource('a1', [7, 8])
        first_filter = session.query.return_value.filter.call_args

    # The filter must be IS NOT NULL, not != 0: NULL != 0 is NULL, not TRUE,
    # so a `!= 0` filter deletes nothing and the next save duplicates.
    rendered = str(first_filter)
    assert 'IS NOT NULL' in rendered.upper() or 'isnot' in rendered, (
        f'delete filter would not match NULL rows: {rendered}'
    )
    assert [link.datasource_id for link in added] == [7, 8]


def test_an_empty_list_clears_the_binding():
    added = []
    session = _session_capturing(added)

    with patch(
        'bisheng.database.models.assistant.get_sync_db_session',
        return_value=_ctx(session),
    ):
        AssistantLinkDao.update_assistant_datasource('a1', [])

    assert added == []
    session.query.return_value.filter.return_value.delete.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_binding.py -q`
Expected: FAIL — `AttributeError: type object 'AssistantLinkDao' has no attribute 'update_assistant_datasource'`

- [ ] **Step 3: 实现 DAO 方法**

在 `assistant.py` 的 `AssistantLinkDao` 类末尾（`update_assistant_knowledge` 之后）追加：

```python
    @classmethod
    def update_assistant_datasource(cls, assistant_id: str, datasource_list: list[int]):
        """Replace this assistant's datasource bindings with `datasource_list`.

        The filter is `IS NOT NULL`, NOT `!= 0` like its three siblings. That is
        not a stylistic difference: `datasource_id` is the only one of the four
        columns whose default is NULL (`F061` added it nullable with no
        server_default), and SQL evaluates `NULL != 0` to NULL rather than TRUE.
        A copied `!= 0` filter would delete nothing, so each save would append
        another set of rows and the assistant would accumulate duplicates.
        """
        with get_sync_db_session() as session:
            session.query(AssistantLink).filter(
                AssistantLink.assistant_id == assistant_id,
                AssistantLink.datasource_id.isnot(None),
            ).delete()
            for one in datasource_list:
                if not one:
                    continue
                session.add(AssistantLink(assistant_id=assistant_id, datasource_id=one))
            session.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_binding.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/backend/bisheng/database/models/assistant.py src/backend/test/datasource/test_assistant_binding.py
git commit -m "feat(datasource): F061 T-31 — write the assistant-datasource binding

The read side shipped in Wave 7 with no way to write one. The delete filter
is IS NOT NULL rather than the != 0 its three siblings use: datasource_id
defaults to NULL, and NULL != 0 is NULL, so the copied form would match no
rows and every save would append duplicates."
```

---

## Task 2: 请求 / 响应 / 保存分支 / 详情分桶

**Files:**
- Modify: `src/backend/bisheng/api/v1/schemas.py:273+`（`AssistantUpdateReq`）与 `:312+`（`AssistantInfo`）
- Modify: `src/backend/bisheng/api/services/assistant.py:536-541`（保存）、`:307-312`（分桶）
- Test: `src/backend/test/datasource/test_assistant_binding.py`（续写）

- [ ] **Step 1: 写会失败的测试**

追加到 `test_assistant_binding.py`：

```python
def test_the_detail_buckets_datasource_links_before_the_else():
    """A datasource row must be recognised, not fall through to the `else` that
    logs 'not expect link info' -- it would still work, but every datasource
    binding would write an error line on every detail read."""
    from bisheng.database.models.assistant import AssistantLink

    links = [
        AssistantLink(assistant_id='a1', tool_id=0, flow_id='', knowledge_id=0, datasource_id=7),
    ]
    assert links[0].datasource_id == 7
    # The bucket order in api/services/assistant.py is an elif chain; the
    # datasource branch must precede the trailing else. Asserted by reading the
    # source rather than by calling it, because the method needs a live session.
    import inspect

    from bisheng.api.services import assistant as assistant_service

    source = inspect.getsource(assistant_service)
    else_at = source.index('not expect link info')
    datasource_at = source.index('one.datasource_id')
    assert datasource_at < else_at, 'datasource branch is missing or after the else'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_binding.py -q`
Expected: FAIL — `ValueError: substring not found`（`one.datasource_id` 尚不存在）

- [ ] **Step 3: 加请求字段**

`api/v1/schemas.py` 的 `AssistantUpdateReq` 中，`knowledge_list` 字段之后加：

```python
    datasource_list: list[int] | None = Field(
        default=None,
        description="F061: bound datasource ids. An empty list clears them; None leaves them unchanged",
    )
```

- [ ] **Step 4: 加响应字段**

同文件 `AssistantInfo` 中，`knowledge_list` 之后加。**返回 id 列表而不是新的 read model**——前端选数据源本就要调 `GET /api/v1/datasource`，再造一个解析模型没有消费者：

```python
    datasource_list: list[int] = Field(
        default_factory=list, description="F061: bound datasource ids"
    )
```

- [ ] **Step 5: 加保存分支**

`api/services/assistant.py:536-541` 那一组 `if xxx is not None:` 中，`knowledge_list` 那条之后加：

```python
        if req.datasource_list is not None:
            AssistantLinkDao.update_assistant_datasource(assistant.id, req.datasource_list)
```

- [ ] **Step 6: 加读取分桶**

同文件 `:301-315` 的 `for one in links:` 循环里，把 `elif one.knowledge_id:` 那条之后、（`else: logger.error("not expect link info")` 之前）插入：

```python
            elif one.datasource_id:
                datasource_list.append(one.datasource_id)
```

并在循环前把 `datasource_list = []` 与 `tool_list = []` 并列声明；在 `AssistantInfo(...)` 构造里加 `datasource_list=datasource_list,`。

> ⚠️ 顺序：`elif` 必须落在 `else` 之前，否则每个数据源绑定每次读详情都刷一条 error 日志（功能不坏，日志被污染）。

- [ ] **Step 7: 跑测试确认通过**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_binding.py -q`
Expected: PASS（3 passed）

- [ ] **Step 8: 提交**

```bash
git add src/backend/bisheng/api/v1/schemas.py src/backend/bisheng/api/services/assistant.py src/backend/test/datasource/test_assistant_binding.py
git commit -m "feat(datasource): F061 T-31 — accept and return datasource bindings on the assistant API

Empty list clears (matching tool_list/flow_list/knowledge_list); None leaves
unchanged. The read side buckets the link rows into ids, and its elif must
stay ahead of the trailing else that logs 'not expect link info'."
```

---

## Task 3: 注入工具到 `AssistantAgent`

**Files:**
- Modify: `src/backend/bisheng/api/services/assistant_agent.py:254-320`（`init_tools`）
- Test: `src/backend/test/datasource/test_assistant_binding.py`（续写）

- [ ] **Step 1: 写会失败的测试**

追加。两条：一条行为测试（绑定/未绑定的真实差别），一条接线锚定（`init_tools` 确实调了它）：

```python
def _payload(user_id=3):
    """A UserPayload, which carries get_visible_tenants. Task 0's assertion
    rejects None and bare LoginUser, because both silently skip the tenant gate."""
    from bisheng.common.dependencies.user_deps import UserPayload

    return UserPayload(user_id=user_id, user_name='', user_role=[])


def _link(datasource_id):
    from bisheng.database.models.assistant import AssistantLink

    return AssistantLink(assistant_id='a1', datasource_id=datasource_id)


async def test_nothing_bound_yields_no_tools():
    """AC-60: every existing assistant keeps behaving exactly as before."""
    from bisheng.datasource.domain.services import conversation_service

    assert await conversation_service.build_tools_for_assistant([], _payload()) == []


async def test_a_bound_and_authorized_datasource_yields_a_tool(monkeypatch):
    from bisheng.datasource.domain.services import conversation_service
    from bisheng.datasource.domain.services.executor import QueryOutcome  # noqa: F401

    async def _allowed(login_user, datasource_ids):
        return list(datasource_ids)

    datasource = MagicMock()
    datasource.id = 7
    datasource.name = '客户订单库'
    datasource.enabled = True

    class _Dao:
        @staticmethod
        async def aget_by_id(_id):
            return datasource

    class _TableDao:
        @staticmethod
        async def alist_by_datasource(_id, enabled_only=True):
            return []

    monkeypatch.setattr(conversation_service, 'authorized_datasource_ids', _allowed)
    monkeypatch.setattr(
        'bisheng.datasource.domain.models.dao.DbDatasourceDao', _Dao
    )
    monkeypatch.setattr(
        'bisheng.datasource.domain.models.dao.DbDatasourceTableDao', _TableDao
    )

    tools = await conversation_service.build_tools_for_assistant([_link(7)], _payload())

    assert [tool.name for tool in tools] == ['datasource_7']


async def test_init_tools_wires_the_datasource_tools():
    """The wiring itself. Asserted on source because driving init_tools needs a
    live assistant plus an LLM; the behavioural half is covered above."""
    import inspect

    from bisheng.api.services import assistant_agent

    source = inspect.getsource(assistant_agent.AssistantAgent.init_tools)
    assert 'build_tools_for_assistant' in source, 'init_tools never asks for datasource tools'
    assert 'UserPayload(' in source, (
        'init_tools must pass a UserPayload -- a bare user id would skip the tenant gate'
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_binding.py -q`
Expected: FAIL — `test_a_bound_and_authorized_datasource_yields_a_tool` 报 `AttributeError`（`authorized_datasource_ids` 是模块级函数，可 monkeypatch；真实失败点在 `DbDatasourceDao` 未被替换的路径上），`test_init_tools_wires_the_datasource_tools` 报 `AssertionError: init_tools never asks for datasource tools`。

> 若 `test_nothing_bound_yields_no_tools` 也失败，说明 Task 0 的断言写错了入参——先修 Task 0。

- [ ] **Step 3: 在 `init_tools` 里注入**

`assistant_agent.py` 的 `init_tools` 中，`links` 取出之后、`for link in links:` 之前，加一段独立的收集（**不要塞进那个 for**，它的 `else: flow_links.append(link)` 语义是「非 tool 即 flow」）：

```python
        # F061 T-31: datasource tools in the SAME list the knowledge tools go
        # into, so the model picks between them within one turn (AC-26).
        from bisheng.common.dependencies.user_deps import UserPayload
        from bisheng.datasource.domain.services.conversation_service import (
            build_tools_for_assistant,
        )

        # A UserPayload, not the bare int: PermissionService.check skips its
        # tenant gate for anything without a callable get_visible_tenants, so a
        # bare id here would drop tenant isolation silently. Same shape the tool
        # permission check below already relies on (skip_unauthorized=True).
        datasource_user = UserPayload(user_id=self.invoke_user_id, user_name="", user_role=[])
        tools.extend(
            await build_tools_for_assistant(links, datasource_user, user_id=self.invoke_user_id)
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_binding.py -q`
Expected: PASS

- [ ] **Step 5: 跑同模块回归，确认没打破既有工具装配**

Run: `cd src/backend && uv run python -m pytest test/datasource/ -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/backend/bisheng/api/services/assistant_agent.py src/backend/test/datasource/test_assistant_binding.py
git commit -m "feat(datasource): F061 T-31 — inject datasource tools into the assistant agent

init_tools is the one place every assistant entry point converges on, so
wiring it here covers the HTTP, websocket, evaluation and publish paths at
once. Collected outside the existing link loop, whose else-branch treats
anything without a tool_id as a flow."
```

---

## Task 4: `last_outcome` 改累积列表

**Files:**
- Modify: `src/backend/bisheng/datasource/domain/langchain/tool.py:73`（字段）与 `:94`、`:113`（赋值）
- Test: `src/backend/test/datasource/test_tool_outcomes.py`（新建）

- [ ] **Step 1: 写会失败的测试**

新建 `test_tool_outcomes.py`：

```python
"""F061 T-31: every query in a turn leaves a trace, not just the last one.

`last_outcome` used to be a single slot, so a model that queried twice in one
turn kept only the final result and the first anchor vanished -- the same class
of silent loss the citation exemption guards against.
"""

from types import SimpleNamespace

from bisheng.datasource.domain.langchain.tool import DatasourceQueryTool
from bisheng.datasource.domain.services.executor import QueryOutcome


def _tool():
    return DatasourceQueryTool(
        name='datasource_7',
        description='在业务库中查询',
        datasource=SimpleNamespace(id=7, name='业务库', tenant_id=1, engine='mysql'),
        tables=[],
        user_id=3,
    )


def _outcome(statement='SELECT 1'):
    return QueryOutcome(statement=statement, columns=['id'], rows=[[1]], row_count=1)


async def test_two_queries_both_leave_an_outcome(monkeypatch):
    tool = _tool()
    outcomes = [_outcome('SELECT 1'), _outcome('SELECT 2')]

    async def _answer_question(datasource, question, tables, **kwargs):
        from bisheng.datasource.domain.services.query_service import AnswerContext

        return AnswerContext(
            answer='ok',
            outcome=outcomes.pop(0),
            injection_mode='full',
            failure_category=None,
        )

    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        _answer_question,
    )

    await tool._arun('第一个问题')
    await tool._arun('第二个问题')

    statements = [o.statement for o in tool.outcomes]
    assert statements == ['SELECT 1', 'SELECT 2'], (
        'a second query overwrote the first -- the first anchor would be lost'
    )


async def test_a_failed_query_adds_no_outcome(monkeypatch):
    tool = _tool()

    async def _answer_question(datasource, question, tables, **kwargs):
        from bisheng.datasource.domain.services.query_service import AnswerContext

        return AnswerContext(
            answer='查询失败：表不存在',
            outcome=None,
            injection_mode='full',
            failure_category='execution',
        )

    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        _answer_question,
    )

    await tool._arun('失败的问题')

    assert tool.outcomes == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_tool_outcomes.py -q`
Expected: FAIL — `AttributeError: 'DatasourceQueryTool' object has no attribute 'outcomes'`

- [ ] **Step 3: 改字段**

`tool.py` 中把 `last_outcome: Any = None` 替换为：

```python
    #: Every successful execution this turn, oldest first. A LIST rather than a
    #: single slot: a model that queries twice in one turn must leave two
    #: anchors, and a last-write-wins slot would silently drop the first.
    outcomes: list[Any] = Field(default_factory=list)
```

- [ ] **Step 4: 改赋值点**

`_arun` 中把 `self.last_outcome = None` 与 `self.last_outcome = context.outcome` 两行相应改为：

```python
        self.last_error_category = None
        # NOTE: `outcomes` is deliberately NOT reset here -- it accumulates
        # across calls within one turn and is cleared by the caller per run.
```

与

```python
        self.outcomes.append(context.outcome)
```

- [ ] **Step 5: 同步类 docstring**

`DatasourceQueryTool` 的 docstring 里那句 ``through `last_outcome`, which the conversation layer reads`` 改为：

```python
    Holds no result state across turns: what came back is returned to the model
    as text, and each structured outcome is appended to `outcomes`, which the
    conversation layer reads when composing the answer's sources.
```

- [ ] **Step 6: 更新既有测试里对旧字段的断言**

`test/datasource/test_tool.py:201` 有 `assert tool.last_outcome is None`。字段改名后这行会让测试变红——**不要**把它删掉，它的意图是「失败的查询不留下 outcome」，改写成等价断言：

```python
    assert tool.outcomes == []
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_tool_outcomes.py test/datasource/test_tool.py -q`
Expected: PASS（两个文件全绿）

- [ ] **Step 7: 提交**

```bash
git add src/backend/bisheng/datasource/domain/langchain/tool.py src/backend/test/datasource/test_tool_outcomes.py
git commit -m "refactor(datasource): F061 T-31 — accumulate query outcomes instead of keeping the last

A model that queried twice in one turn kept only the final result, so the
first anchor vanished. Same class of silent loss the citation exemption
guards against."
```

---

## Task 5: 锚点桥接 —— 扩 `AssistantCitationToolWrapper`

**Files:**
- Modify: `src/backend/bisheng/api/services/assistant_agent.py:51-160`（wrapper）、`:335`（`wrap_citation_tool`）
- Test: `src/backend/test/datasource/test_assistant_anchor.py`（新建）

- [ ] **Step 1: 写会失败的测试**

新建 `test_assistant_anchor.py`：

```python
"""F061 T-31: a database answer carries an anchor the UI can expand (AC-29/31/34).

The anchor is built from the SAME execution the model saw, taken off the tool's
accumulated outcomes rather than re-derived -- re-deriving would eventually
disagree with what ran.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bisheng.api.services.assistant_agent import AssistantCitationToolWrapper
from bisheng.citation.domain.schemas.citation_schema import CitationType
from bisheng.datasource.domain.langchain.tool import DatasourceQueryTool
from bisheng.datasource.domain.services.executor import QueryOutcome


def _tool_with_outcome():
    tool = DatasourceQueryTool(
        name='datasource_7',
        description='在业务库中查询',
        datasource=SimpleNamespace(id=7, name='客户订单库', tenant_id=1, engine='mysql'),
        tables=[],
        user_id=3,
    )
    tool.outcomes.append(
        QueryOutcome(
            statement='SELECT id FROM orders',
            columns=['id'],
            rows=[[1]],
            row_count=1,
        )
    )
    return tool


def test_wrapping_a_datasource_tool_produces_a_datasource_item():
    # CitationRegistryCollector lives in citation_prompt_helper, NOT in the
    # schemas module.
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CitationRegistryCollector,
    )

    collector = CitationRegistryCollector()
    wrapped = AssistantCitationToolWrapper.wrap(_tool_with_outcome(), collector)

    items = wrapped.datasource_citation_items()

    assert len(items) == 1
    item = items[0]
    assert item.type == CitationType.DATASOURCE
    assert item.sourcePayload['datasourceId'] == 7
    assert item.sourcePayload['datasourceName'] == '客户订单库'
    assert item.sourcePayload['statement'] == 'SELECT id FROM orders'
    assert item.sourcePayload['rows'] == [[1]]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_anchor.py -q`
Expected: FAIL — `AttributeError: 'AssistantCitationToolWrapper' object has no attribute 'datasource_citation_items'`

- [ ] **Step 3: `_tables_from` 提为公开**

它目前是 `DatasourceExecutor` 上的 `@staticmethod _tables_from(statement)`（`executor.py:255`），且已被 `:187` 调用。锚点要跨模块用它，**不要跨模块调下划线方法**——重命名为 `tables_from_statement`，同文件内 `:187` 的调用点一并改。

- [ ] **Step 4: 加 wrapper 分支**

在 `AssistantCitationToolWrapper` 中加判定与取值方法。注意 `self.tool.outcomes` —— 字段在 Task 4 已改为复数，**先确认 Task 4 已合入**：

```python
    def _is_datasource_tool(self) -> bool:
        return isinstance(self.tool, DatasourceQueryTool)

    def datasource_citation_items(self) -> list[CitationRegistryItemSchema]:
        """One anchor per execution this tool performed (AC-31/34).

        Built from the tool's accumulated outcomes -- the same objects the model
        read -- rather than from a re-derivation, which would drift.
        """
        if not self._is_datasource_tool():
            return []
        items: list[CitationRegistryItemSchema] = []
        for outcome in self.tool.outcomes:
            payload = DatasourceCitationPayloadSchema(
                datasourceId=self.tool.datasource.id,
                datasourceName=self.tool.datasource.name,
                tables=tables_from_statement(outcome.statement),
                statement=outcome.statement,
                columns=list(outcome.columns),
                rows=[list(row) for row in outcome.rows],
                truncated=outcome.truncated,
            )
            items.append(
                CitationRegistryItemSchema(
                    citationId=f'datasource_{self.tool.datasource.id}_{len(items)}',
                    type=CitationType.DATASOURCE,
                    sourcePayload=payload.model_dump(),
                )
            )
        return items
```

顶部补 import（与既有 import 并列）：

```python
from bisheng.datasource.domain.langchain.tool import DatasourceQueryTool
from bisheng.datasource.domain.schemas.citation import DatasourceCitationPayloadSchema
from bisheng.datasource.domain.services.executor import tables_from_statement
```

**`assistant_agent.py` 目前没有 import `CitationType`**（已核实：`:18` 只有 `CitationRegistryItemSchema`，`:19-21` 从 `citation_prompt_helper` 取的三项里也没有它）。把 `:18` 那行改成：

```python
from bisheng.citation.domain.schemas.citation_schema import CitationRegistryItemSchema, CitationType
```

- [ ] **Step 5: 放行包装**

`wrap_citation_tool`（`:335`）中，把数据源工具一并纳入：

```python
    def wrap_citation_tool(self, tool: BaseTool) -> BaseTool:
        if (
            tool.name == "web_search"
            or hasattr(tool, "knowledge_retriever_tool")
            or isinstance(tool, DatasourceQueryTool)
        ):
            return AssistantCitationToolWrapper.wrap(tool, self.citation_registry_collector)
        return tool
```

- [ ] **Step 6: 让收集点带上数据源项**

`collect_citation_registry_items`（`:350`）在遍历 `self.tools` 时，对数据源 wrapper 也取项：

```python
        for tool in self.tools:
            if isinstance(tool, AssistantCitationToolWrapper):
                candidate_items.extend(tool.citation_registry_items)
                candidate_items.extend(tool.datasource_citation_items())
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_anchor.py -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/backend/bisheng/api/services/assistant_agent.py src/backend/bisheng/datasource/domain/services/executor.py src/backend/test/datasource/test_assistant_anchor.py
git commit -m "feat(datasource): F061 T-31 — bridge query outcomes into citation anchors

Extends the existing wrapper rather than collecting after the run: the
wrapper is applied to every tool in init_tools, which is what makes the
HTTP, websocket, evaluation and publish paths all carry the anchor.
tables_from_statement is promoted from private because the anchor needs
it across a module boundary."
```

---

## Task 6: 锚点豁免文本标记过滤

**Files:**
- Modify: `src/backend/bisheng/citation/domain/services/citation_prompt_helper.py:282-294`
- Test: `src/backend/test/datasource/test_assistant_anchor.py`（续写）

- [ ] **Step 1: 写会失败的测试**

追加到 `test_assistant_anchor.py`。

> ⚠️ **引用标记必须是真实格式**：`{citationId}:1`（私用区 Unicode 角标）。`_split_citation_key`（`citation_prompt_helper.py:179-187`）要求 `:` 存在且两侧都非空。**若标记写错，`extract_citation_ids_from_text` 返回空集，函数会退化成「全部保留」——测试将不修即过，白测一场。** 下面用常量拼接，避免手写转义。

```python
def test_datasource_anchors_survive_the_text_marker_filter():
    """The tool renders a plain markdown table and emits no citation marker, so
    a turn that ALSO cited a document -- and therefore has real markers -- would
    drop the datasource anchor. AC-31/34 require it to persist."""
    from bisheng.citation.domain.schemas.citation_schema import (
        CitationRegistryItemSchema,
        CitationType,
    )
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CITATION_END_MARKER,
        CITATION_START_MARKER,
        select_registry_items_for_persistence,
    )

    def _marker(citation_id: str) -> str:
        return f'{CITATION_START_MARKER}{citation_id}:1{CITATION_END_MARKER}'

    rag = CitationRegistryItemSchema(
        citationId='rag1', type=CitationType.RAG, sourcePayload={'fileId': 1}
    )
    datasource = CitationRegistryItemSchema(
        citationId='ds1',
        type=CitationType.DATASOURCE,
        sourcePayload={'datasourceId': 7, 'statement': 'SELECT 1'},
    )
    answer = f'按文档回答{_marker("rag1")}'

    # Guard the guard: if the marker is malformed the function returns
    # everything and this test would pass without the exemption being added.
    assert extract_citation_ids_from_text(answer) == {'rag1'}

    kept = select_registry_items_for_persistence([rag, datasource], answer)

    assert datasource in kept, 'the datasource anchor was dropped by the marker filter'
    assert rag in kept


def test_unreferenced_rag_items_are_still_filtered():
    """The exemption is for datasources only -- do not loosen RAG behaviour."""
    from bisheng.citation.domain.schemas.citation_schema import (
        CitationRegistryItemSchema,
        CitationType,
    )
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CITATION_END_MARKER,
        CITATION_START_MARKER,
        select_registry_items_for_persistence,
    )

    cited = CitationRegistryItemSchema(
        citationId='rag1', type=CitationType.RAG, sourcePayload={'fileId': 1}
    )
    uncited = CitationRegistryItemSchema(
        citationId='rag2', type=CitationType.RAG, sourcePayload={'fileId': 2}
    )
    answer = f'{CITATION_START_MARKER}rag1:1{CITATION_END_MARKER}'

    kept = select_registry_items_for_persistence([cited, uncited], answer)

    assert kept == [cited]
```

文件顶部补上 `extract_citation_ids_from_text` 的 import（第一条测试的「守卫的守卫」用得到）：

```python
from bisheng.citation.domain.services.citation_prompt_helper import (
    extract_citation_ids_from_text,
)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_anchor.py -q`
Expected: FAIL — `assert datasource in kept` 失败（数据源项被标记过滤丢掉）。**若这条反而通过，说明标记格式写错了**，回 Step 1 检查 `assert extract_citation_ids_from_text(...) == {'rag1'}` 那行。

- [ ] **Step 3: 实现豁免**

把 `select_registry_items_for_persistence` 的返回语句：

```python
    return [item for item in items if item.citationId in citation_ids]
```

改为：

```python
    # Datasource anchors are exempt (F061 T-31). The datasource tool renders a
    # plain markdown table and emits no citation marker, so an answer that also
    # cited a document -- and therefore HAS markers -- would drop the anchor
    # even though the query really ran. RAG and web items keep the old rule.
    return [
        item
        for item in items
        if item.citationId in citation_ids or item.type == CitationType.DATASOURCE
    ]
```

该文件目前**没有** import `CitationType`（`citation_prompt_helper.py:11` 只取了 `CitationRegistryItemSchema`），且它已 import 了 `CitationRegistryItemSchema` —— 改成一次取两个：

```python
from bisheng.citation.domain.schemas.citation_schema import CitationRegistryItemSchema, CitationType
```

> 若这造成循环导入，退而用函数内局部导入（同文件已有多处 `from ... import` 延迟导入的先例）。

`assistant_agent.py` 同样缺 `CitationType`（已核实：`:18` 只 import 了 `CitationRegistryItemSchema`），Task 5 Step 4 需要一并补上。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_anchor.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 跑 citation 与 sensitive_word 两侧回归**

Run: `cd src/backend && uv run python -m pytest test/sensitive_word/ test/datasource/ -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/backend/bisheng/citation/domain/services/citation_prompt_helper.py src/backend/test/datasource/test_assistant_anchor.py
git commit -m "fix(citation): F061 T-31 — keep datasource anchors past the text-marker filter

The datasource tool emits no citation marker, so any answer that also cited
a document dropped its database anchor silently. RAG and web keep the old
marker rule; only DATASOURCE is exempt."
```

---

## Task 7: AC-63 —— 数据库答案仍过输出围栏

**Files:**
- Test: `src/backend/test/datasource/test_assistant_anchor.py`（续写）

> 这是 design §6.2 记的依赖「`ChatGuardService`（F056）：数据库答案文本必须同样过关（AC-63）」。**目前零测试锚定**——spec 要求了，没人验证过。

- [ ] **Step 1: 先确认护栏挂在哪个函数上**

Run: `cd src/backend && grep -n "output_guard\|ChatGuardService" bisheng/common/chat/client.py | head`

记录实际函数名（README 记为 `self.output_guard(self.chat_id, answer)`，在 `client.py:429` 附近）。

- [ ] **Step 2: 写测试**

追加到 `test_assistant_anchor.py`。**先读**上一步得到的实际签名再落笔；下面给了按当前签名的写法：

```python
async def test_a_database_answer_still_goes_through_the_output_guard():
    """AC-63: adding a datasource must not create a path that bypasses the
    compliance guard. The datasource answer is assembled by the same client
    code as any other, so it must pass the same guard."""
    import inspect

    from bisheng.common.chat import client as chat_client

    source = inspect.getsource(chat_client)
    # The guard call must still wrap the answer on the way out; if a future
    # change routes datasource answers around it, this fails.
    assert 'output_guard(' in source, 'the output guard call disappeared from the chat client'
```

- [ ] **Step 3: 跑测试**

Run: `cd src/backend && uv run python -m pytest test/datasource/test_assistant_anchor.py -q`
Expected: PASS

> 说明：这是**存在性锚定**，不是行为验证。真正的行为验证（数据库答案含敏感词被截断/打码）需活后端与配置好的策略，属 `/e2e-test` 范围，本计划不冒充已覆盖。若你要行为测试，在此加一条用 `ChatGuardService` 直接驱动答案文本的用例。

- [ ] **Step 4: 提交**

```bash
git add src/backend/test/datasource/test_assistant_anchor.py
git commit -m "test(datasource): F061 T-31 — anchor that database answers still pass the output guard

AC-63 had no test at all. This is an existence anchor, not behaviour
verification -- the behavioural case needs a live backend and belongs to
/e2e-test."
```

---

## Task 8: 全量回归 + 文档收口

- [ ] **Step 1: 跑本特性与相邻模块**

Run: `cd src/backend && uv run python -m pytest test/datasource/ test/sensitive_word/ test/worker/ -q`
Expected: PASS（`test/worker/` 含工作区那个未提交的 305 行新测试；若其中有失败，先确认是否为本次改动引入）

- [ ] **Step 2: 只对自己改过的文件跑 ruff**

Run: `cd src/backend && uv run ruff check bisheng/datasource/ bisheng/api/services/assistant_agent.py bisheng/api/services/assistant.py bisheng/citation/domain/services/citation_prompt_helper.py`

**不要**跑 `ruff format` 于目录——后端已提交文件是单引号，pyproject 要求双引号，目录级 format 会波及他人文件。

- [ ] **Step 3: 更新 tasks.md 状态**

把 `features/v2.7.0/061-datasource-qa/tasks.md` 的 T-31 勾掉，并在「实现」行备注里把 T-31 从「余下」移到「已完成」，写明：绑定写入侧 + 工具注入 + 锚点桥接已落地，前端（T-35~T-41）与 T-42/T-43/T-44 仍未做。

- [ ] **Step 4: 提交**

```bash
git add features/v2.7.0/061-datasource-qa/tasks.md
git commit -m "docs(sdd): F061 — T-31 chat-side wiring landed"
```

---

## 完成后仍**未**覆盖的部分（不要误以为 F061 已完）

| 项 | 状态 |
|---|---|
| T-35~T-39 平台管理端 UI | 未开工（`pages/DatasourcePage/` 不存在） |
| T-40/T-41 客户端选择与回答区 | 未开工（两个 SPA 无法共享组件） |
| T-42 工作流 Agent 节点收敛 | 未开工（`agent.py:151` 仍是内联 `SqlAgentParams`） |
| T-44 不变量验证 | 未开工（`test_invariants.py` 不存在） |
| AC-24 工作台半边 | 已登记为 spec 缺口 |
| 锚点在前端的渲染（展开/下载） | 后端已产，UI 未消费 |
