"""Prompt construction for classification and extraction.

Everything here is built from per-tenant data. There is no document-type literal
anywhere in this module, and there must never be one: this feature exists to
generalize the hardcoded enum that `file_encoding.py::CLASSIFY_PROMPT` ships
with, so reintroducing fixed types would defeat the point (design decision 2).

The chat LLM layer has no structured-output support, so both prompts state the
JSON shape explicitly and the caller validates what comes back.
"""

from __future__ import annotations

from typing import Any

_TYPE_HINTS = {
    'string': '文本',
    'number': '数字',
    'date': '日期',
    'boolean': '是/否',
}


def build_classify_prompt(candidates: list[dict[str, Any]]) -> str:
    """System prompt asking the model to pick one of ``candidates``.

    ``candidates`` are the tenant's *enabled* templates (name + doc_type +
    description). With an empty list the prompt still builds -- the caller
    rejects the request before calling the model, but a well-formed prompt keeps
    that decision in one place.
    """
    lines = [
        '# 角色',
        '你是一个文档类型识别助手。你的任务是判断一份文档属于下面哪一类。',
        '',
        '# 可选类型',
    ]

    if candidates:
        for candidate in candidates:
            lines.append(f"- {candidate['doc_type']}（{candidate['name']}）：{candidate.get('description', '')}")
    else:
        lines.append('- （当前没有可用的文档类型）')

    lines.extend([
        '',
        '# 判定原则',
        '- 只能从上述类型中选择一个，不允许自造类型。',
        '- 若文档与任何一类都不相符，仍须给出你认为最接近的一项，并给出较低的置信度。',
        '- 置信度是你对该判断的把握，取值 0 到 1 之间的小数。',
        '',
        '# 输出要求',
        '- 只输出 JSON，不要输出解释或多余文字。',
        '- 格式严格为：{"doc_type": "<类型标识>", "confidence": <0~1 的小数>}',
    ])
    return '\n'.join(lines)


def build_extract_messages(
    document_text: str,
    field_schema: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Chat messages asking the model to fill ``field_schema`` from the document.

    Required fields are called out explicitly so the model knows what it cannot
    omit; the validator enforces it regardless, but saying so up front is what
    keeps the first attempt from failing.
    """
    required = [f for f in field_schema if f.get('required')]
    optional = [f for f in field_schema if not f.get('required')]

    def _describe(fields: list[dict[str, Any]]) -> list[str]:
        out = []
        for field in fields:
            hint = _TYPE_HINTS.get(field.get('type', 'string'), field.get('type', 'string'))
            out.append(f"- {field['key']}（{field.get('label', field['key'])}，{hint}）")
        return out

    lines = [
        '# 角色',
        '你是一个文档字段抽取助手。请从文档中抽取指定字段。',
        '',
        '# 需要抽取的字段',
    ]

    if required:
        lines.append('## 必填（必须给出值）')
        lines.extend(_describe(required))
    if optional:
        if required:
            lines.append('')
        lines.append('## 选填（文档中没有就留空或省略）')
        lines.extend(_describe(optional))
    if not required and not optional:
        lines.append('（该模板未定义任何字段）')

    lines.extend([
        '',
        '# 抽取原则',
        '- 只依据文档内容作答，不要推测或编造。',
        '- 文档中没有的信息，不要凭空填写。',
        '- 日期按 YYYY-MM-DD 输出。',
        '',
        '# 输出要求',
        '- 只输出 JSON，不要输出解释或多余文字。',
        '- 格式严格为：{"字段标识": "值", ...}，键名必须是上面列出的字段标识。',
    ])

    return [
        {'role': 'system', 'content': '\n'.join(lines)},
        {'role': 'user', 'content': f'文档内容：\n\n{document_text}'},
    ]


def build_retry_messages(
    base_messages: list[dict[str, str]],
    previous_output: str,
    errors: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Append the model's failed attempt plus the specific problems with it.

    Without this the retry is a coin flip; with it the model is told exactly
    which key was wrong and why (spec AC-26 requires naming the failed field).
    """
    problem_lines = []
    for err in errors:
        if err.get('reason') == 'missing_required':
            problem_lines.append(f"- {err['field_key']}（{err.get('label', err['field_key'])}）缺失，但它是必填字段")
        else:
            problem_lines.append(
                f"- {err['field_key']}（{err.get('label', err['field_key'])}）类型不符："
                f"要求 {err['expected']}，实际收到 {err['actual']!r}"
            )

    return base_messages + [
        {'role': 'assistant', 'content': previous_output},
        {
            'role': 'user',
            'content': (
                '上次输出不符合要求，存在以下问题：\n'
                + '\n'.join(problem_lines)
                + '\n\n请修正后重新输出，只输出 JSON。'
            ),
        },
    ]
