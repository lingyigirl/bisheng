import json
import math

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from bisheng.evaluation.domain.services.answer_correctness import compute_answer_correctness


class _StubLLM:
    """Minimal stand-in exposing .generate() like a langchain chat model."""

    def __init__(self, payload_text: str):
        self._text = payload_text

    def generate(self, messages, **kwargs):
        gens = [[ChatGeneration(message=AIMessage(content=self._text))] for _ in messages]
        return LLMResult(generations=gens)


class _ScriptedLLM:
    """Return one pre-baked batch of judge outputs per generate() call."""

    def __init__(self, batches: list[list[str]]):
        self._batches = batches
        self.calls = 0
        self.seen: list[list] = []

    def generate(self, messages, **kwargs):
        self.seen.append(messages)
        texts = self._batches[min(self.calls, len(self._batches) - 1)]
        self.calls += 1
        gens = [[ChatGeneration(message=AIMessage(content=text))] for text in texts]
        return LLMResult(generations=gens)


def _judge_payload(overlap, answer_only, gt_only, contra=None) -> str:
    item = {
        "statements that are present in both the answer and the ground truth": overlap,
        "statements present in the answer but not found in the ground truth": answer_only,
        "relevant statements found in the ground truth but omitted in the answer": gt_only,
    }
    if contra is not None:
        item["statements present in the answer that contradict the ground truth"] = contra
    return json.dumps([item])


def test_scores_match_formula():
    payload = json.dumps(
        [
            {
                "statements that are present in both the answer and the ground truth": ["a", "b"],
                "statements present in the answer but not found in the ground truth": ["c"],
                "relevant statements found in the ground truth but omitted in the answer": [],
            }
        ]
    )
    result = compute_answer_correctness(
        _StubLLM(payload), question=["q"], answer=["x"], ground_truths=[["g"]], human_prompt=""
    )
    # tp=2, fp=1, fn=0 -> f1 = 2/(2+0.5*1)=0.8 ; precision=2/3 ; recall=1.0
    assert result["statements_num_overlap"][0] == 2
    assert result["statements_num_answer_only"][0] == 1
    assert result["statements_num_gt_only"][0] == 0
    assert math.isclose(result["answer_f1"][0], 0.8)
    assert math.isclose(result["answer_precision"][0], 2 / 3)
    assert result["answer_recall"][0] == 1.0
    assert result["question"] == ["q"]
    assert result["ground_truths"] == [["g"]]


def test_unparseable_output_yields_nan():
    result = compute_answer_correctness(
        _StubLLM("not json at all"), question=["q"], answer=["x"], ground_truths=[["g"]], human_prompt=""
    )
    assert math.isnan(result["answer_f1"][0])
    assert result["statements_overlap"][0] == ""


def test_empty_statement_lists_yield_zero_not_nan():
    """0/0 denominators (judge measured nothing) -> 0.0, not NaN (2026-09-05)."""
    payload = json.dumps(
        [
            {
                "statements that are present in both the answer and the ground truth": [],
                "statements present in the answer but not found in the ground truth": [],
                "relevant statements found in the ground truth but omitted in the answer": [],
            }
        ]
    )
    result = compute_answer_correctness(
        _StubLLM(payload), question=["q"], answer=[""], ground_truths=[["g"]], human_prompt=""
    )
    assert result["answer_f1"][0] == 0.0
    assert result["answer_precision"][0] == 0.0
    assert result["answer_recall"][0] == 0.0


def test_markdown_fenced_output_is_parsed():
    payload = "```json\n" + _judge_payload(["a"], ["b"], [], contra=[]) + "\n```"
    result = compute_answer_correctness(
        _StubLLM(payload), question=["q"], answer=["x"], ground_truths=[["g"]], human_prompt=""
    )
    assert result["judge_status"] == ["success"]
    assert result["answer_precision"][0] == 0.5


def test_json_embedded_in_prose_is_parsed():
    payload = "Here is the result:\n" + _judge_payload(["a"], [], [], contra=[]) + "\nDone."
    result = compute_answer_correctness(
        _StubLLM(payload), question=["q"], answer=["x"], ground_truths=[["g"]], human_prompt=""
    )
    assert result["judge_status"] == ["success"]
    assert result["answer_recall"][0] == 1.0


def test_bare_json_object_is_parsed():
    payload = json.loads(_judge_payload(["a"], [], [], contra=[]))[0]
    result = compute_answer_correctness(
        _StubLLM(json.dumps(payload)), question=["q"], answer=["x"], ground_truths=[["g"]], human_prompt=""
    )
    assert result["judge_status"] == ["success"]


def test_retry_recovers_from_unparseable_first_output():
    llm = _ScriptedLLM([["not json"], [_judge_payload(["a"], ["b"], [], contra=[])]])
    result = compute_answer_correctness(llm, question=["q"], answer=["x"], ground_truths=[["g"]], human_prompt="")
    assert result["judge_status"] == ["success"]
    assert result["judge_error"] == [""]
    assert result["judge_attempts"] == [2]
    assert llm.calls == 2
    retry_text = llm.seen[1][0][0].content
    assert "could not be used" in retry_text
    assert "statements present in the answer that contradict the ground truth" in retry_text


def test_failed_output_keeps_reason_and_raw_output():
    result = compute_answer_correctness(
        _StubLLM("not json at all"), question=["q"], answer=["x"], ground_truths=[["g"]], human_prompt=""
    )
    assert result["judge_status"] == ["failed"]
    assert "不是合法 JSON" in result["judge_error"][0]
    assert result["judge_raw_output"] == ["not json at all"]
    assert result["judge_attempts"] == [3]


def test_missing_fourth_bucket_is_partial_but_scores_core_metrics():
    result = compute_answer_correctness(
        _StubLLM(_judge_payload(["a"], ["b"], [])),
        question=["q"],
        answer=["x"],
        ground_truths=[["g"]],
        human_prompt="",
    )
    assert result["judge_status"] == ["partial"]
    assert "矛盾语句字段" in result["judge_error"][0]
    assert result["answer_precision"][0] == 0.5
    assert math.isnan(result["statements_contradicted_num"][0])
    assert result["statements_num_answer"][0] == 2
    assert result["judge_raw_output"][0] != ""
