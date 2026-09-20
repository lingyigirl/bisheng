"""Read a model id out of a tenant's system-model slot.

Shared because two features now resolve slots the same way (F058 document
extraction, F061 datasource SQL generation), and the parsing rules below are
not obvious enough to be worth re-deriving per feature. They were learned the
hard way — F058 shipped a slot that never resolved because of the first one:

**The slot column holds JSON TEXT, not a bare number.** Every writer stores
``json.dumps(payload)`` and every existing reader deserializes first
(``llm/domain/services/llm.py``). ``int()`` on ``'{"model_id": 5}'`` raises, so
a reader that skips ``json.loads`` sees a configured slot as permanently
unconfigured and fails every request with a "not configured" message while the
operator stares at a correctly filled-in form.

**A bare integer is still accepted**, because a hand-written row should not
silently break — and because that tolerance is what makes the failure mode of a
future writer change (storing a plain number) a non-event rather than an outage.

**Unparseable is None, never an exception.** Callers treat None as "not
configured" and fall back; raising would turn a bad config row into a 500.

The ``extract_model_id`` alias is inherited from F058, where the slot payload
shape was not settled when it shipped. Kept because rows written then may still
carry it.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger


def parse_model_slot_value(value: Any) -> int | None:
    """``{"model_id": N}`` (as JSON text or a dict) or a bare number -> N; else None."""
    if value is None:
        return None

    payload = value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            # Not JSON -- fall through and try it as a plain number.
            payload = value

    if isinstance(payload, dict):
        model_id = payload.get('model_id') or payload.get('extract_model_id')
        try:
            return int(model_id) if model_id is not None else None
        except (TypeError, ValueError):
            return None

    try:
        return int(payload)
    except (TypeError, ValueError):
        return None


def resolve_slot_model_id(tenant_id: int | None, key: str) -> int | None:
    """Resolve a tenant's configured model for `key`, or None.

    None means "unconfigured OR unreadable", which callers deliberately do not
    distinguish: the user-facing behaviour is the same (fall back), and telling
    them apart in the response would leak whether another tenant's config row
    exists. The difference IS logged, because an operator whose slot is set but
    unparseable would otherwise go looking at the wrong thing.
    """
    if tenant_id is None:
        return None

    from bisheng.llm.domain.models.tenant_system_model_config import (
        TenantSystemModelConfigDao,
    )

    try:
        value, _inherited, _blocked = TenantSystemModelConfigDao.resolve(tenant_id, key)
    except Exception:
        logger.warning('model slot %s resolve failed for tenant=%s', key, tenant_id, exc_info=True)
        return None

    model_id = parse_model_slot_value(value)
    if model_id is None and value:
        logger.warning('model slot %s is set but unparseable: %r', key, value)
    return model_id
