"""Render extracted fields through a template's render form.

Deliberately a small standalone implementation rather than a reuse of either
existing template path in the repo:

- ``workflow/nodes/prompt_template.py::PromptTemplateParser`` uses ``{{#var#}}``
  and actively *strips* variables back to ``{var}`` -- the opposite of filling
  them in. Reusing it would silently leave placeholders in the output.
- ``workflow/nodes/report/docx_replace.py::DocxReplacer`` emits a .docx; this
  feature needs readable text.

Because the syntax is parsed here, dangling references can be rejected at save
time (`extract_referenced_keys`) instead of surfacing at run time.
"""

from __future__ import annotations

import re
from typing import Any

# `{{ key }}` with optional inner whitespace. Key charset mirrors what the
# field-schema validator accepts, so anything matchable here is checkable
# against the schema.
_PLACEHOLDER_RE = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')


def extract_referenced_keys(template: str | None) -> set[str]:
    """Every field key the render form refers to.

    Used at save time to reject a template whose render form mentions a field
    that its field schema does not define (spec AC-03) -- a run-time-only check
    would let the mistake ship and then silently render blanks.
    """
    if not template:
        return set()
    return set(_PLACEHOLDER_RE.findall(template))


def render(template: str | None, fields: dict[str, Any] | None) -> str:
    """Fill ``template`` with ``fields``.

    A field that was not extracted renders as empty text: no error, no
    leftover placeholder, and no effect on the remaining fields (spec AC-29).
    By the time this runs, required-field completeness has already been enforced
    by the validator -- so anything missing here is optional by construction.
    """
    if not template:
        return ''

    fields = fields or {}

    def replace(match: re.Match) -> str:
        value = fields.get(match.group(1))
        if value is None:
            return ''
        if isinstance(value, str):
            return value
        return str(value)

    return _PLACEHOLDER_RE.sub(replace, template)
