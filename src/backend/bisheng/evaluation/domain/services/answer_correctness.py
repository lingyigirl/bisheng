"""LangChain reimplementation of the former bisheng_ragas AnswerCorrectnessBisheng metric.

Output is byte-identical to the previous ragas-based implementation: the same nine
fields, the same f1/precision/recall formulas, and the same default few-shot prompt.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from json_repair import repair_json
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate

# F067 (design 决策 6): the 4th bucket — statements from the answer that contradict
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


def _parse(text: str) -> list:
    try:
        obj = json.loads(repair_json(text))
    except Exception:
        return []
    return obj if isinstance(obj, list) and obj else []


def compute_answer_correctness(
    llm: BaseChatModel,
    question: list[str],
    answer: list[str],
    ground_truths: list[list[str]],
    human_prompt: str = "",
) -> dict[str, list[Any]]:
    """Return a dict-of-lists with question/answer/ground_truths plus the metric fields."""
    prompt_template = build_judge_prompt_template(human_prompt)
    message_batches = []
    for q, a, g in zip(question, answer, ground_truths):
        msg = prompt_template.format(question=q, ground_truth=g[0], answer=a)
        message_batches.append(ChatPromptTemplate.from_messages([msg]).format_messages())

    llm_result = llm.generate(message_batches)

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
        # F067: hallucination inputs — contradicted claims (⊆ answer-only) and the
        # answer's total statement count (overlap + answer-only).
        "statements_contradicted": [],
        "statements_contradicted_num": [],
        "statements_num_answer": [],
        "answer_f1": [],
        "answer_precision": [],
        "answer_recall": [],
    }

    for generations in llm_result.generations:
        prediction = _parse(generations[0].text)
        if prediction:
            item = prediction[0]
            overlap = item.get(_KEY_MAP["TP"], "")
            answer_only = item.get(_KEY_MAP["FP"], "")
            gt_only = item.get(_KEY_MAP["FN"], "")
            contradicted = item.get(_KEY_MAP["CONTRA"], "")
            tp, fp, fn = (len(x) if isinstance(x, list) else np.nan for x in (overlap, answer_only, gt_only))
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
