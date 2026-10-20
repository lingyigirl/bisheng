"""F078 T-04: error-code range, locale keys and faceted handler."""

import asyncio
import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from bisheng.common.errcode import managed_agent as errors
from bisheng.main import handle_managed_agent_error

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCALES = ("zh-Hans", "en", "ja")


def _error_classes():
    return [
        obj
        for obj in vars(errors).values()
        if isinstance(obj, type)
        and issubclass(obj, errors.ManagedAgentErrorBase)
        and obj is not errors.ManagedAgentErrorBase
    ]


def test_error_codes_are_12101_to_12114():
    codes = sorted(cls.Code for cls in _error_classes())
    assert codes == list(range(12101, 12115))


def test_every_error_has_three_language_copy():
    for lang in LOCALES:
        path = REPO_ROOT / "frontend" / "packages" / "locales" / "src" / "api_errors" / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [str(cls.Code) for cls in _error_classes() if str(cls.Code) not in data]
        assert not missing, f"{lang} missing api_errors copy: {missing}"


def _request(*, open_api: bool) -> Request:
    scope = {"type": "http", "method": "POST", "path": "/api/v2/managed-agent/invoke"}
    if open_api:
        scope["open_api_call"] = True
    return Request(scope)


def test_handler_returns_real_status_on_v2_and_envelope_on_v1():
    exc = errors.ManagedAgentRateLimitedError()

    response_v2 = asyncio.run(handle_managed_agent_error(_request(open_api=True), exc))
    response_v1 = asyncio.run(handle_managed_agent_error(_request(open_api=False), exc))

    assert isinstance(response_v2, JSONResponse)
    assert response_v2.status_code == 429
    assert response_v1.status_code == 200
    body = json.loads(response_v1.body)
    assert body["status_code"] == 12112
