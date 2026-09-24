"""Who still references a datasource — asked before a delete.

A datasource is referenced from two places, and they are stored differently:

* **Assistants** — `assistantlink.datasource_id`, a real column with an index.
* **Workflows** — inside `flow.data`, a JSON blob. The Agent node's `sql_agent`
  param carries the id; there is no column and no foreign key, so the only way
  to find it is to scan the flows and parse them.

Both matter before a delete, because removing a datasource makes every bound
node stop querying and the Agent node does it **silently** —
`AgentNode._init_datasource_tool` returns no tool rather than raising, so a flow
simply stops answering from the database with nothing on screen to say why. The
answer a user gets is a plausible answer that ignored the database.

Deliberately NOT counted: `db_query_log`, `db_query_failure` and citation
anchors. They carry the id too, but they record what HAPPENED, and the delete
path keeps them on purpose (spec AC-62) so past answers still render. Counting
them would mark every datasource that was ever queried as "in use" and make
deletion impossible.

Dual-DB (constitution C2): the flow scan prefilters with a `CAST(... AS CHAR)
LIKE` and parses in Python. MySQL's JSON-path functions have no DM8 equivalent,
and the column is JSON on MySQL but a text-ish type on DM8. The CAST is not
decorative — see the note at the query itself: without it the predicate matches
nothing at all on MySQL.

Scan cost: the `LIKE` cannot use an index — it renders each row's JSON to text
to match. The flow table is small and this runs on an admin action (a delete, or
the list that precedes it), never on a chat turn, so the full scan is the right
trade for not needing a schema change and a backfill to keep in sync.
"""

from __future__ import annotations

from sqlalchemy import String, cast, select

from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.core.database import get_async_db_session

#: The Agent node parameter holding a bound datasource (`SqlAgentParams`).
SQL_AGENT_PARAM_KEY = 'sql_agent'

#: Prefilter for the flow scan. Not the answer — the id is read from the parsed
#: params below; this only keeps flows that cannot possibly match out of the
#: JSON walk.
_DATASOURCE_TOKEN = 'datasource_id'


def _flow_binds_datasource(data: object, datasource_id: int) -> bool:
    """Whether any Agent node in this flow binds `datasource_id`.

    Walks the shape the flow builder writes and `api/services/workflow.py` reads:
    `nodes[*].data.group_params[*].params[*]` → the `sql_agent` param.

    Compared as strings so a JSON number and a JSON string both match — the id
    has been written through a few paths over the feature's life and a numeric
    mismatch here would silently report "not referenced".
    """
    if not isinstance(data, dict):
        return False
    for node in data.get('nodes') or []:
        node_data = node.get('data') or {}
        for group in node_data.get('group_params') or []:
            for param in group.get('params') or []:
                if param.get('key') != SQL_AGENT_PARAM_KEY:
                    continue
                bound = (param.get('value') or {}).get('datasource_id')
                if bound is not None and str(bound) == str(datasource_id):
                    return True
    return False


async def alist_references(datasource_id: int) -> dict[str, list[dict]]:
    """The assistants and flows still bound to this datasource.

    Returns `{'assistants': [{'id', 'name'}], 'flows': [{'id', 'name'}]}`, each
    sorted by name so a caller can render the list as-is.

    Runs inside `strict_tenant_filter()`: the guard is a tenant admin acting on
    their own datasource, and the names in the reply must not include another
    tenant's assistants. An assistant that was deleted (`is_delete = 1`) is
    skipped — its binding row survives, and reporting it would block deleting a
    datasource on account of something an admin already threw away.
    """
    from bisheng.database.models.assistant import Assistant, AssistantLink
    from bisheng.database.models.flow import Flow

    with strict_tenant_filter():
        async with get_async_db_session() as session:
            assistant_rows = (
                await session.exec(
                    select(Assistant.id, Assistant.name)
                    .join(AssistantLink, AssistantLink.assistant_id == Assistant.id)
                    .where(
                        AssistantLink.datasource_id == datasource_id,
                        Assistant.is_delete == 0,
                    )
                )
            ).all()

            # The `LIKE` MUST go through a CAST. `Flow.data` is a JSON column and
            # `.like()` binds its parameter with the JSON type, so MySQL compares
            # JSON to JSON instead of text and matches NOTHING — measured: the
            # identical predicate returns 10 rows over a raw connection and 0
            # through SQLAlchemy. A plain `.like()` here is not slow, it is
            # silently empty, which would let a datasource bound to a workflow be
            # deleted. `CAST(... AS CHAR)` is what makes the comparison textual.
            #
            # The result set is Rows, not entities — a plain `select()` here is
            # SQLAlchemy's, not SQLModel's (see feedback_event.backfill_comment
            # for what mixing those up costs).
            flow_rows = (
                await session.exec(
                    select(Flow.id, Flow.name, Flow.data).where(
                        cast(Flow.data, String).like(f'%{_DATASOURCE_TOKEN}%')
                    )
                )
            ).all()

    return {
        'assistants': sorted(
            ({'id': row[0], 'name': row[1]} for row in assistant_rows),
            key=lambda one: one['name'] or '',
        ),
        'flows': sorted(
            ({'id': row[0], 'name': row[1]} for row in flow_rows if _flow_binds_datasource(row[2], datasource_id)),
            key=lambda one: one['name'] or '',
        ),
    }
