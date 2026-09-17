"""Field-level validation of model output against a template's field schema.

The extraction prompt asks the model for JSON, but there is no structured-output
support in the chat LLM layer, so correctness has to be checked here. The error
payload is deliberately specific (which key, what was expected, what arrived) --
the retry loop feeds it back to the model, which is what makes retrying worth
anything.
"""

from __future__ import annotations

import re
from typing import Any

# Accepted field types, v1 scalar only. Nested types (invoice line items) are
# explicitly out of scope for this feature.
FIELD_TYPES = ('string', 'number', 'date', 'boolean')

_DATE_PATTERNS = (
    re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$'),
    re.compile(r'^\d{4}/\d{1,2}/\d{1,2}$'),
    re.compile(r'^\d{4}年\d{1,2}月\d{1,2}日$'),
)

_TRUE_TOKENS = {'true', 'yes', '是', '1'}
_FALSE_TOKENS = {'false', 'no', '否', '0'}


def _is_missing(value: Any) -> bool:
    """Empty string and None both mean "not extracted" (spec AC-15).

    Callers cannot distinguish "the model had nothing to put here" from "the
    model omitted the key", and neither can the user -- so they are one case.
    """
    return value is None or (isinstance(value, str) and value.strip() == '')


def _check_type(value: Any, field_type: str) -> bool:
    if field_type == 'string':
        return isinstance(value, str)

    if field_type == 'number':
        # bool is a subclass of int -- reject it explicitly or True would pass.
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value.strip())
                return True
            except ValueError:
                return False
        return False

    if field_type == 'boolean':
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.strip().lower() in (_TRUE_TOKENS | _FALSE_TOKENS)
        return False

    if field_type == 'date':
        if not isinstance(value, str):
            return False
        return any(p.match(value.strip()) for p in _DATE_PATTERNS)

    # Unknown declared type: the template should have been rejected at save
    # time, so treat it as a definition error rather than silently passing.
    return False


def validate_fields(
    fields: dict[str, Any],
    schema: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    """Validate ``fields`` against ``schema``.

    Returns ``(ok, errors)`` where each error is
    ``{'field_key', 'label', 'expected', 'actual'}``.
    """
    errors: list[dict[str, Any]] = []

    for field in schema or []:
        key = field.get('key')
        if not key:
            continue
        expected = field.get('type', 'string')
        required = bool(field.get('required', False))
        value = fields.get(key)

        if _is_missing(value):
            if required:
                errors.append({
                    'field_key': key,
                    'label': field.get('label', key),
                    'expected': expected,
                    'actual': None,
                    'reason': 'missing_required',
                })
            continue

        if not _check_type(value, expected):
            errors.append({
                'field_key': key,
                'label': field.get('label', key),
                'expected': expected,
                'actual': value,
                'reason': 'type_mismatch',
            })

    return (len(errors) == 0, errors)
