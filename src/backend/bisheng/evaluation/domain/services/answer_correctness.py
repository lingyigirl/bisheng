"""LangChain reimplementation of the former bisheng_ragas AnswerCorrectnessBisheng metric.

Output is byte-identical to the previous ragas-based implementation: the same nine
fields, the same f1/precision/recall formulas, and the same default few-shot prompt.
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np
from json_repair import repair_json
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.prompts import HumanMessagePromptTemplate
from loguru import logger

# F090 (design 决策 6): the 4th bucket — statements from the answer that contradict
# the ground truth. Appended to the judge prompt OUTSIDE the {placeholders} so it
# reaches the model whether the caller uses the default few-shot prompt or their own.
HALLUCINATION_INSTRUCTION = """

Also add a fourth list to the same JSON object: the statements from the answer that
directly contradict the ground truth. It is a strict subset of "statements present in
the answer but not found in the ground truth" — a claim that merely paraphrases or omits
detail is NOT a contradiction, and a statement must not be repeated across lists. Use
exactly this key:
  "statements present in the answer that contradict the ground truth": [...]
"""

_CORRECTNESS_TEMPLATE_TEXT = """
Extract following from given question and ground truth

Question:What powers the sun and what is its primary function?
Answer: The sun is powered by nuclear fission, similar to nuclear reactors on Earth, and its primary function is to provide light to the solar system.
Ground truth: The sun is actually powered by nuclear fusion, not fission. In its core, hydrogen atoms fuse to form helium, releasing a tremendous amount of energy. This energy is what lights up the sun and provides heat and light, essential for life on Earth. The sun's light also plays a critical role in Earth's climate system and helps to drive the weather and ocean currents.
Extracted statements:
[
{{
  "statements that are present in both the answer and the ground truth": ["The sun's primary function is to provide light"],
  "statements present in the answer but not found in the ground truth": ["The sun is powered by nuclear fission", "similar to nuclear reactors on Earth"],
  "relevant statements found in the ground truth but omitted in the answer": ["The sun is powered by nuclear fusion, not fission", "In its core, hydrogen atoms fuse to form helium, releasing a tremendous amount of energy", "This energy provides heat and light, essential for life on Earth", "The sun's light plays a critical role in Earth's climate system", "The sun helps to drive the weather and ocean currents"]
}}
]

Question: What is the boiling point of water?
Answer: The boiling point of water is 100 degrees Celsius at sea level.
Ground truth: The boiling point of water is 100 degrees Celsius (212 degrees Fahrenheit) at sea level, but it can change with altitude.
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": ["The boiling point of water is 100 degrees Celsius at sea level"],
    "statements present in the answer but not found in the ground truth": [],
    "relevant statements found in the ground truth but omitted in the answer": ["The boiling point can change with altitude", "The boiling point of water is 212 degrees Fahrenheit at sea level"]
  }}
]

Question: 公司2021年的研发费用占营业收入的比例是多少？
Answer: 根据提供的信息，公司2021年的研发费用占营业收入的比例为15.86%。
Ground truth: 根据公司招股书披露数据，公司2021年的研发费用占营业收入的比例为15.86%。
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": ["公司2021年的研发费用占营业收入的比例为15.86%"],
    "statements present in the answer but not found in the ground truth": [],
    "relevant statements found in the ground truth but omitted in the answer": []
  }}
]

Question: 达梦2021年的息税折旧摊销前利润是多少？
Answer: 达梦2021年的息税折旧摊销前利润为49,189.87万元。
Ground truth: 根据达梦数据库招股书披露数据，达梦2021年的息税折旧摊销前利润为49,189.85万元。
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": [],
    "statements present in the answer but not found in the ground truth": ["达梦2021年的息税折旧摊销前利润为49,189.87万元"],
    "relevant statements found in the ground truth but omitted in the answer": ["根据达梦数据库招股书披露数据，达梦2021年的息税折旧摊销前利润为49,189.85万元"]
  }}
]

Question: 达梦2022年的应收账款周转率是多少？
Answer: 根据提供的信息，无法得知达梦2022年的应收账款周转率。
Ground truth: 很抱歉，达梦尚未披露2022年报数据。
Extracted statements:
[
  {{
    "statements that are present in both the answer and the ground truth": ["无法得知达梦2022年的应收账款周转率"],
    "statements present in the answer but not found in the ground truth": [],
    "relevant statements found in the ground truth but omitted in the answer": [],
  }}
]


Question:{question}
Answer: {answer}
Ground truth: {ground_truth}
Extracted statements:"""

CORRECTNESS_PROMPT = HumanMessagePromptTemplate.from_template(_CORRECTNESS_TEMPLATE_TEXT + HALLUCINATION_INSTRUCTION)

_KEY_MAP = {
    "TP": "statements that are present in both the answer and the ground truth",
    "FP": "statements present in the answer but not found in the ground truth",
    "FN": "relevant statements found in the ground truth but omitted in the answer",
    "CONTRA": "statements present in the answer that contradict the ground truth",
}


def build_judge_prompt_template(human_prompt: str = "") -> HumanMessagePromptTemplate:
    """Final judge template with the 4th-bucket instruction folded in (design 决策 6).

    Used for **both** paths — the default few-shot prompt and a user-supplied one —
    so a custom prompt does not silently drop hallucination counting.
    """
    text = human_prompt if human_prompt else _CORRECTNESS_TEMPLATE_TEXT
    return HumanMessagePromptTemplate.from_template(text + HALLUCINATION_INSTRUCTION)


# A judge output that cannot be parsed makes every correctness metric N/A, which
# silently drops the question from the aggregates. Retry a couple of times before
# giving up: most failures are transient formatting noise (truncation, prose around
# the JSON, a missing key), not a systematic model limitation.
JUDGE_MAX_ATTEMPTS = 3
_RAW_OUTPUT_LIMIT = 2000
_CORE_KEYS = (_KEY_MAP["TP"], _KEY_MAP["FP"], _KEY_MAP["FN"])


def _json_candidates(text: str) -> list[str]:
    """Likely JSON payloads, best first: raw, fenced block, then outermost span."""
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    for part in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE):
        part = part.strip()
        if part:
            candidates.append(part)
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    return candidates


def _parse_json(text: str) -> Any:
    for candidate in _json_candidates(text):
        try:
            return json.loads(candidate)
        except Exception:
            pass
        try:
            return json.loads(repair_json(candidate))
        except Exception:
            pass
    return None


def _parse_output(text: str) -> tuple[dict | None, str, bool]:
    """Parse one judge response.

    Returns ``(item, reason, complete)``. ``item`` is ``None`` only when the core
    three statement lists are unusable; a missing contradiction bucket keeps the
    item but marks it incomplete, so accuracy/F1/recall can still be scored.
    """
    if not text.strip():
        return None, "裁判输出为空", False
    obj = _parse_json(text)
    if obj is None:
        return None, "裁判输出不是合法 JSON", False
    if isinstance(obj, list):
        if not obj:
            return None, "裁判返回空数组", False
        obj = obj[0]
    if not isinstance(obj, dict):
        return None, "裁判输出的 JSON 不是对象", False

    missing = [key for key in _CORE_KEYS if not isinstance(obj.get(key), list)]
    if missing:
        return None, f"裁判输出缺少必要字段: {', '.join(missing)}", False

    contra_key = _KEY_MAP["CONTRA"]
    if not isinstance(obj.get(contra_key), list):
        return obj, f"裁判输出缺少矛盾语句字段: {contra_key}", False
    return obj, "", True


def _retry_prompt(base: str, reason: str) -> str:
    """Re-ask with the failure reason, spelling out the exact JSON shape again."""
    keys = "\n".join(f"- {_KEY_MAP[key]}" for key in ("TP", "FP", "FN", "CONTRA"))
    return (
        f"{base}\n\n"
        f"Your previous response could not be used ({reason}). "
        "Return ONLY a JSON array containing exactly one object with these keys:\n"
        f"{keys}\n"
        "Every value must be a JSON array of strings. Do not use markdown fences "
        "and do not add any explanation before or after the JSON."
    )


def compute_answer_correctness(
    llm: BaseChatModel,
    question: list[str],
    answer: list[str],
    ground_truths: list[list[str]],
    human_prompt: str = "",
) -> dict[str, list[Any]]:
    """Return a dict-of-lists with question/answer/ground_truths plus the metric fields."""
    prompt_template = build_judge_prompt_template(human_prompt)
    prompt_texts: list[str] = []
    for q, a, g in zip(question, answer, ground_truths, strict=True):
        msg = prompt_template.format(question=q, ground_truth=g[0], answer=a)
        prompt_texts.append(msg.to_string() if hasattr(msg, "to_string") else str(msg))

    count = len(prompt_texts)
    items: list[dict | None] = [None] * count
    complete = [False] * count
    attempts = [0] * count
    raw_outputs = [""] * count
    item_reason = ["裁判未返回可解析结果"] * count
    last_reason = list(item_reason)
    pending = list(range(count))

    for attempt in range(1, JUDGE_MAX_ATTEMPTS + 1):
        if not pending:
            break
        message_batches = [
            [
                HumanMessage(
                    content=prompt_texts[index]
                    if attempt == 1
                    else _retry_prompt(prompt_texts[index], last_reason[index])
                )
            ]
            for index in pending
        ]
        llm_result = llm.generate(message_batches)
        for index, generations in zip(pending, llm_result.generations, strict=False):
            raw = generations[0].text if generations else ""
            item, reason, ok = _parse_output(raw)
            attempts[index] = attempt
            raw_outputs[index] = raw
            last_reason[index] = reason
            if item is not None:
                items[index] = item
                item_reason[index] = reason
            complete[index] = ok
        pending = [index for index in pending if not complete[index]]

    out: dict[str, list[Any]] = {
        "question": list(question),
        "answer": list(answer),
        "ground_truths": list(ground_truths),
        "statements_gt_only": [],
        "statements_num_gt_only": [],
        "statements_answer_only": [],
        "statements_num_answer_only": [],
        "statements_overlap": [],
        "statements_num_overlap": [],
        # F090: hallucination inputs — contradicted claims (⊆ answer-only) and the
        # answer's total statement count (overlap + answer-only).
        "statements_contradicted": [],
        "statements_contradicted_num": [],
        "statements_num_answer": [],
        "answer_f1": [],
        "answer_precision": [],
        "answer_recall": [],
        # Diagnostics persisted to the result CSV so an N/A row can be explained
        # after the run (the raw judge output is otherwise discarded).
        "judge_status": [],
        "judge_error": [],
        "judge_raw_output": [],
        "judge_attempts": [],
    }

    for index, item in enumerate(items):
        status = "success" if complete[index] else ("partial" if item is not None else "failed")
        reason = "" if complete[index] else (item_reason[index] if item is not None else last_reason[index])
        out["judge_status"].append(status)
        out["judge_attempts"].append(attempts[index])
        out["judge_error"].append(reason)
        out["judge_raw_output"].append("" if status == "success" else raw_outputs[index][:_RAW_OUTPUT_LIMIT])
        if status != "success":
            logger.warning(
                "judge output unusable for question#{} after {} attempt(s): {} raw={!r}",
                index + 1,
                attempts[index],
                reason,
                raw_outputs[index][:300],
            )

        if item is not None:
            overlap = item.get(_KEY_MAP["TP"], [])
            answer_only = item.get(_KEY_MAP["FP"], [])
            gt_only = item.get(_KEY_MAP["FN"], [])
            contradicted = item.get(_KEY_MAP["CONTRA"])
            tp, fp, fn = (len(x) for x in (overlap, answer_only, gt_only))
            contra_num = len(contradicted) if isinstance(contradicted, list) else np.nan
            out["statements_overlap"].append(str(overlap))
            out["statements_answer_only"].append(str(answer_only))
            out["statements_gt_only"].append(str(gt_only))
            out["statements_num_overlap"].append(tp)
            out["statements_num_answer_only"].append(fp)
            out["statements_num_gt_only"].append(fn)
            out["statements_contradicted"].append(str(contradicted))
            out["statements_contradicted_num"].append(contra_num)
            # answer's total statements = overlap + answer-only (contradicted ⊆ answer-only,
            # so it is never added on top — no double counting, design 坑 5).
            out["statements_num_answer"].append(tp + fp)
            # 0/0 denominators (judge measured nothing at all, e.g. empty answer)
            # count as 0.0 — "nothing earned" — instead of NaN, which poisons the
            # aggregate mean (2026-09-05 evaluation NaN cleanup).
            # Judge parse failures keep NaN and are excluded at aggregation time.
            denom_precision = tp + fp
            denom_recall = tp + fn
            denom_f1 = tp + 0.5 * (fp + fn)
            out["answer_precision"].append(tp / denom_precision if denom_precision else 0.0)
            out["answer_recall"].append(tp / denom_recall if denom_recall else 0.0)
            out["answer_f1"].append(tp / denom_f1 if denom_f1 else 0.0)
        else:
            out["statements_overlap"].append("")
            out["statements_answer_only"].append("")
            out["statements_gt_only"].append("")
            out["statements_num_overlap"].append(np.nan)
            out["statements_num_answer_only"].append(np.nan)
            out["statements_num_gt_only"].append(np.nan)
            out["statements_contradicted"].append("")
            out["statements_contradicted_num"].append(np.nan)
            out["statements_num_answer"].append(np.nan)
            out["answer_f1"].append(np.nan)
            out["answer_precision"].append(np.nan)
            out["answer_recall"].append(np.nan)

    return out
