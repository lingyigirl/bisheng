"""F052 AC-05: node-run records must never leak into chat history.

The chat-history API reads the `chatmessage` table only; F052's node-run
records live in their own table (`workflownode_run`). These tests pin that
separation so a future refactor cannot silently merge them.
"""

from bisheng.database.models.message import ChatMessage
from bisheng.database.models.workflow_node_run import WorkflowNodeRun


def test_node_run_is_not_a_chat_message():
    assert WorkflowNodeRun.__tablename__ != ChatMessage.__tablename__
    assert WorkflowNodeRun.__tablename__ == "workflownoderun"


def test_chat_history_dao_queries_chat_message_only():
    """afilter_message_by_chat_id is the history read path and must stay on ChatMessage."""
    import inspect

    from bisheng.database.models.message import ChatMessageDao

    src = inspect.getsource(ChatMessageDao.afilter_message_by_chat_id)
    assert "select(ChatMessage)" in src
    assert "WorkflowNodeRun" not in src
