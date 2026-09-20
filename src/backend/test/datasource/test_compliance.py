"""F061 T-43: a database answer goes through the same compliance chain (AC-63).

AC-63 says a database answer obeys the same conversation-compliance constraints
as any other answer — the sensitive-word gate and the logging rules are not
affected by this feature.

`test_assistant_anchor.py` already anchors the ANSWER path with an AST scan of
`handle_gpts_message` (the guard runs on `answer`, before it is persisted and
sent). What this file adds is the part that check cannot see: whether a
DATASOURCE's output reaches the user through some OTHER channel — a tool-result
event, a log line, a progress frame — which would let raw database content
bypass the gate that the answer text has to pass.

That is a real risk here and not a hypothetical one: a datasource tool's output
IS the result table, so any channel that forwards tool output to the client
would leak rows unguarded. Three channels exist in the codebase and this file
pins each one:

  1. The assistant ws path (where datasources live). Its `on_tool_end` is a
     no-op, so tool output never reaches the client at all.
  2. The workstation SSE path, which DOES forward tool output as
     `agent_tool_call.results` — and has no datasource tools, so the exposure is
     empty today. The gap is recorded here so the day datasource tools are added
     to that agent (the AC-24 workbench gap), this test is what fails and says
     why.
  3. The log lines. `on_tool_end` logs the output; a result table in a log is
     the INV-19 clause 3 boundary (statements and summaries, not result sets).

Asserted by scanning, for the same reason as `test_invariants.py`: what is being
claimed is that a channel does NOT carry something.
"""

import inspect
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


# ── 1. the assistant path (where datasource tools are) ───────────────────────


def test_the_assistant_ws_callback_does_not_forward_tool_output():
    """The channel a datasource actually rides on.

    `AsyncGptsLLMCallbackHandler.on_tool_end` is the handler the assistant agent
    streams through. It must stay a no-op: the moment it forwards `output` to
    the client, a datasource's raw result table would reach the UI without
    passing the sensitive-word gate — the gate runs on the ANSWER, and a tool
    frame is not the answer.

    If this fails because someone implemented it deliberately, the fix is NOT to
    revert — it is to route whatever it now forwards through the same guard, and
    then update this test to assert that it does.
    """
    from bisheng.api.v1.callback import AsyncGptsLLMCallbackHandler

    source = inspect.getsource(AsyncGptsLLMCallbackHandler.on_tool_end)
    body = source.split('"""')[-1] if '"""' in source else source

    forwarded = [
        token
        for token in ('send_json', 'websocket', 'send_response', 'yield', 'emit')
        if token in body
    ]
    assert not forwarded, (
        f'the assistant callback now forwards tool output ({forwarded}) — a '
        'datasource tool result is a raw result table, and it would reach the '
        'client without passing the sensitive-word gate (AC-63)'
    )


def test_the_assistant_ws_callback_leaves_on_tool_end_empty():
    """The same claim stated so it cannot pass by accident: the method body is
    `pass` plus logging. A future edit that adds real work here fails this even
    if it does not use one of the tokens above."""
    from bisheng.api.v1.callback import AsyncGptsLLMCallbackHandler

    source = inspect.getsource(AsyncGptsLLMCallbackHandler.on_tool_end)
    # Strip the docstring, then every statement must be a logger call.
    body = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
    statements = [
        line.strip()
        for line in body.split('\n')
        if line.strip() and not line.strip().startswith(('async def', 'def', '#', ')'))
    ]
    non_logging = [s for s in statements if not s.startswith(('logger.', 'return', 'pass'))]
    assert not non_logging, (
        f'AsyncGptsLLMCallbackHandler.on_tool_end does more than log: {non_logging}'
    )


# ── 2. the workstation SSE path, and why its exposure is empty today ─────────


def test_the_workstation_path_forwards_tool_output_and_has_no_datasource_tool():
    """Pins the coupling between two facts that are each fine alone.

    The workstation SSE path DOES put tool output in `agent_tool_call.results`
    and persists it. That is not an AC-63 problem today ONLY because that agent
    has no datasource tools — its source selection comes from the request, and
    datasources are not part of it (spec AC-24's registered gap).

    So this test asserts the second half: no datasource tool is reachable from
    the workstation agent. When the AC-24 gap is closed and datasource tools are
    added there, this fails — and it should, because that change has to decide
    how tool output is guarded before the results reach the client.
    """
    from bisheng.workstation.domain.services import chat_service

    source = inspect.getsource(chat_service)

    assert 'DatasourceQueryTool' not in source and 'build_datasource_tools' not in source, (
        'the workstation agent now builds datasource tools. It forwards tool '
        'output to the client as `agent_tool_call.results` with no sensitive-word '
        'gate — so this must be resolved (route the results through the guard, or '
        'stop forwarding them) before a datasource rides that path (AC-63).'
    )


# ── 3. the logging boundary ──────────────────────────────────────────────────


def test_the_datasource_feature_does_not_log_result_rows():
    """INV-19 clause 3 continues the INV-11 boundary: the query log and the
    failure trail hold STATEMENTS and summaries, never result sets. The same
    boundary has to hold for ordinary log lines, which is where a debugging
    addition would most naturally put them.

    Scans the datasource package for a log statement that interpolates a row or
    result variable. A statement is fine and expected (`_record_failure` logs
    what was tried); a result set is not.
    """
    suspects = []
    for path in (BACKEND / 'bisheng' / 'datasource').rglob('*.py'):
        if '__pycache__' in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
            if not re.search(r'logger\.(debug|info|warning|error|exception)', line):
                continue
            # A logger call naming rows/result data. `rows` appears in the
            # executor for the COUNT and the truncation flag, which are
            # summaries — so match the variable rather than the word.
            if re.search(r'\brows\b\s*[=,)]|\brow\b\s*[=,)]|outcome\.rows|result_rows', line):
                suspects.append(f'{path.name}:{lineno}: {line.strip()}')

    assert not suspects, (
        'a datasource log line interpolates result rows — INV-19 clause 3 keeps '
        'result sets out of the logs, not just out of the tables:\n'
        + '\n'.join(suspects)
    )


#: Tokens that mean "this column carries result data", matched as WHOLE WORDS
#: within the snake_case name. Substring matching is too broad — `datasource_id`
#: contains "data" — and exact-name matching is too narrow: a `result_rows`
#: column stayed green under an exact set. Splitting on `_` gets both right.
_RESULT_TOKENS = {'row', 'rows', 'result', 'results', 'dataset', 'record', 'records', 'data'}

#: Status details that name a count rather than carrying content. `row_count`
#: says how many rows there were, not what they were, and the model docstring
#: relies on it being allowed.
_STATUS_WORDS = {'count', 'total', 'num', 'len', 'size'}


def _result_bearing_columns(model) -> set[str]:
    """Column names that look like they hold a result set."""
    offending = set()
    for column in model.__table__.columns:
        words = column.name.lower().split('_')
        if any(word in _STATUS_WORDS for word in words):
            continue
        if set(words) & _RESULT_TOKENS:
            offending.add(column.name)
    return offending


def test_the_query_log_table_stores_no_result_set():
    """The table half of the same boundary, asserted on the model so a later
    column addition fails here rather than in a customer's audit."""
    from bisheng.datasource.domain.models.query_log import DbQueryLog

    # Matched by TOKEN, not by exact name. A set of exact names ('rows',
    # 'result') misses the natural spelling -- `result_rows` -- which is how the
    # column would actually be added. Verified by mutation: the exact-match
    # version stayed green with a real `result_rows` column present.
    offending = _result_bearing_columns(DbQueryLog)
    assert not offending, (
        f'db_query_log gained {sorted(offending)} — INV-19 clause 3 says the log '
        'holds the statement and a status, never the result set'
    )


def test_the_failure_table_stores_no_result_set():
    """The failure list exists to drive schema-description work (AC-42), so it
    holds the statements that were tried and a category. Same boundary."""
    from bisheng.datasource.domain.models.query_failure import DbQueryFailure

    offending = _result_bearing_columns(DbQueryFailure)
    assert not offending, (
        f'db_query_failure gained {sorted(offending)} — failed queries produced '
        'no result set, and the trail must not become a place to accumulate one'
    )
