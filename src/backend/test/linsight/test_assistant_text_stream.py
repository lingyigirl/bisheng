"""覆盖 AC: AC-01-AC-07, AC-20: real model chunks and replay text semantics."""

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from bisheng.linsight.domain.services.replay_compaction import ReplayCompactor
from bisheng.linsight.domain.services.stream_event_mapper import StreamEventMapper


def test_text_deltas_and_final_snapshot_do_not_duplicate():
    mapper = StreamEventMapper("task", text_protocol=True)
    first = mapper.normalize("messages", (AIMessageChunk(content="Hello ", id="a"), {}))
    second = mapper.normalize("messages", (AIMessageChunk(content="world", id="a"), {}))
    end = mapper.normalize("updates", {"model": {"messages": [AIMessage(content="Hello world", id="a")]}})
    assert [e.output for e in first + second if e.step_type == "assistant"] == ["Hello ", "world"]
    assert first[0].call_id == second[0].call_id
    assert any(e.status == "end" for e in end)
    assert not mapper.normalize("updates", {"model": {"messages": [AIMessage(content="Hello world", id="a")]}})


def test_mixed_reasoning_content_and_tools_survive():
    mapper = StreamEventMapper("task", text_protocol=True)
    events = mapper.normalize(
        "messages",
        (
            AIMessageChunk(
                content="Checking now.",
                id="a",
                additional_kwargs={"reasoning_content": "Reasoning"},
                tool_call_chunks=[{"name": "lookup", "id": "tool-1", "args": "{}", "index": 0}],
            ),
            {},
        ),
    )
    assert {"thinking", "assistant", "tool"} <= {e.step_type for e in events}


def test_internal_summary_stream_is_hidden_without_splitting_real_reasoning():
    mapper = StreamEventMapper("task", text_protocol=True)
    first = mapper.normalize(
        "messages", (AIMessageChunk(id="answer", content="", additional_kwargs={"reasoning_content": "Checking"}), {})
    )
    internal = AIMessageChunk(
        id="summary", content="Internal summary", additional_kwargs={"reasoning_content": "Summarizing history"}
    )
    assert mapper.normalize("messages", (internal, {"lc_source": "summarization"})) == []
    assert not mapper.text_mapper.last_answer_text
    second = mapper.normalize(
        "messages", (AIMessageChunk(id="answer", content="", additional_kwargs={"reasoning_content": " carefully"}), {})
    )
    assert first[0].call_id == second[0].call_id
    answer = mapper.normalize("messages", (AIMessageChunk(id="answer", content="Verified"), {}))
    assert any(event.output == "Verified" for event in answer)


def test_namespaces_and_runs_have_separate_text_identity():
    one = StreamEventMapper("task", text_protocol=True)
    two = StreamEventMapper("task", text_protocol=True)
    msg = (AIMessageChunk(content="x", id="same"), {})
    a = one.normalize("messages", msg, namespace=("member-a",))[0]
    b = one.normalize("messages", msg, namespace=("member-b",))[0]
    c = two.normalize("messages", msg, namespace=("member-a",))[0]
    assert len({a.call_id, b.call_id, c.call_id}) == 3
    assert a.extra_info["namespace"] == "member-a"


def test_legacy_protocol_does_not_emit_text_or_tool_results_as_text():
    mapper = StreamEventMapper("task")
    assert not mapper.normalize("messages", (AIMessageChunk(content="hidden"), {}))
    enabled = StreamEventMapper("task", text_protocol=True)
    assert not enabled.normalize("messages", (ToolMessage(content="not prose", tool_call_id="x"), {}))


def test_nonstreaming_model_emits_snapshot_once():
    mapper = StreamEventMapper("task", text_protocol=True)
    events = mapper.normalize("updates", {"model": {"messages": [AIMessage(content="Whole answer", id="a")]}})
    assert any(e.output == "Whole answer" and e.extra_info["text_mode"] == "snapshot" for e in events)
    assert mapper.last_answer_step_id


def test_replay_compaction_keeps_text_modes_and_tail():
    compactor = ReplayCompactor()
    for i, (mode, output) in enumerate([("delta", "a"), ("delta", "b"), ("snapshot", "abc"), ("end", "")]):
        compactor.append(
            {
                "event_type": "task_execute_step",
                "event_id": f"1-{i}",
                "data": {
                    "call_id": "answer",
                    "step_type": "assistant",
                    "output": output,
                    "extra_info": {"text_mode": mode},
                    "status": "end" if mode == "end" else "start",
                },
            }
        )
    events = compactor.finish()
    assert events[0]["data"]["output"] == "ab"
    assert events[1]["data"]["output"] == "abc"
    assert events[-1]["data"]["extra_info"]["text_mode"] == "end"


def test_authoritative_full_message_can_correct_an_already_closed_stream():
    from bisheng.linsight.domain.services.assistant_text_mapper import AssistantTextMapper

    mapper = AssistantTextMapper("task", "run")
    mapper.message(AIMessageChunk(id="message", content="partial", response_metadata={"finish_reason": "stop"}), None)
    events = mapper.message(AIMessage(id="message", content="complete answer"), None)
    assert [event.extra_info["text_mode"] for event in events] == ["snapshot", "end"]
    assert mapper.last_answer_text == "complete answer"
    assert mapper.message(AIMessage(id="message", content="complete answer"), None) == []
