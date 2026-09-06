"""BishengASR.ainvoke must forward the model-level `prompt` config to the ASR client."""

from unittest.mock import AsyncMock

import pytest

from bisheng.common.constants.enums.telemetry import ApplicationTypeEnum
from bisheng.core.ai.base import BaseASRClient
from bisheng.llm.domain.llm.asr import BishengASR
from bisheng.llm.domain.models.llm_server import LLMModel


class RecordingASR(BaseASRClient):
    """Fake ASR client that records the kwargs passed to transcribe."""

    def __init__(self):
        self.kwargs = None

    async def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return "ok"

    async def _transcribe(self, audio, language=None, model=None, **kwargs):
        raise NotImplementedError("transcribe is overridden in tests")


class TraditionalASR(RecordingASR):
    """Fake ASR client returning Traditional Chinese, as LLM-based ASR sometimes does."""

    async def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return "這是一個測試，後天出發前往臺灣。"


async def _make_asr(model_config, client=None):
    return BishengASR(
        model_id=1,
        asr=client or RecordingASR(),
        model_info=LLMModel(model_type="asr", config=model_config),
        app_id="test",
        app_type=ApplicationTypeEnum.ASR,
        app_name="test",
        user_id=1,
    )


@pytest.fixture(autouse=True)
def _neutralize_decorator(monkeypatch):
    """ainvoke is wrapped by wrapper_bisheng_model_limit_check_async, which
    touches limit-checking and telemetry; neutralize for a pure unit test."""
    from bisheng.llm.domain import utils as llm_utils

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(llm_utils, "bisheng_model_limit_check", _noop)
    monkeypatch.setattr(llm_utils, "upload_telemetry_log", lambda *a, **k: None)
    monkeypatch.setattr(BishengASR, "update_model_status", AsyncMock())
    yield


@pytest.mark.asyncio
async def test_model_config_prompt_is_forwarded():
    asr = await _make_asr({"prompt": "请输出简体中文。"})
    result = await asr.ainvoke(b"fake audio")
    assert result == "ok"
    assert asr.asr.kwargs["prompt"] == "请输出简体中文。"


@pytest.mark.asyncio
async def test_caller_prompt_wins_over_model_config():
    asr = await _make_asr({"prompt": "请输出简体中文。"})
    await asr.ainvoke(b"fake audio", prompt="caller prompt")
    assert asr.asr.kwargs["prompt"] == "caller prompt"


@pytest.mark.asyncio
async def test_no_prompt_injected_when_config_missing():
    asr = await _make_asr(None)
    await asr.ainvoke(b"fake audio")
    assert "prompt" not in asr.asr.kwargs

    asr_empty = await _make_asr({})
    await asr_empty.ainvoke(b"fake audio")
    assert "prompt" not in asr_empty.asr.kwargs


@pytest.mark.asyncio
async def test_traditional_output_normalized_to_simplified():
    asr = await _make_asr({"prompt": "请输出简体中文。"}, client=TraditionalASR())
    result = await asr.ainvoke(b"fake audio")
    assert result == "这是一个测试，后天出发前往台湾。"


@pytest.mark.asyncio
async def test_simplified_output_left_unchanged():
    asr = await _make_asr({}, client=RecordingASR())
    result = await asr.ainvoke(b"fake audio")
    assert result == "ok"
