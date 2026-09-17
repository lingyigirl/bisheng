"""Text-path parsing: route decision and the "no parser" failure.

Only the `text` route is covered here. The `ocr` route needs a real recognition
service and is handled separately (see T-35's test-degradation note) -- which is
what lets everything else in this feature run offline.

Covers spec AC-21 (both input kinds accepted) and AC-22 (a document with a text
layer is used directly, with no external service involved).
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.doc_extract import DocExtractNoParserError

from bisheng.doc_extract.domain.services.parse_adapter import (
    MIN_USABLE_TEXT_LENGTH,

    ROUTE_TEXT,
    ParseAdapter,
)

ADAPTER = 'bisheng.doc_extract.domain.services.parse_adapter'


def _fake_pipeline_result(text: str):
    class _Doc:
        page_content = text

    class _Result:
        documents = [_Doc()] if text else []

    return _Result()


@pytest.fixture()
def tmp_doc(tmp_path: Path) -> Path:
    path = tmp_path / 'license.txt'
    path.write_text('营业执照正文内容', encoding='utf-8')
    return path


async def test_text_layer_above_threshold_uses_text_route(tmp_doc):
    long_text = '营业执照内容' * 50
    with patch(f'{ADAPTER}.TempFilePipeline') as pipeline_cls:
        pipeline_cls.return_value.arun = AsyncMock(return_value=_fake_pipeline_result(long_text))
        text, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )

    assert route == ROUTE_TEXT
    assert text == long_text


async def test_short_text_is_not_treated_as_usable_content(tmp_doc):
    """A scanned PDF can carry a thin garbage text layer. Below the threshold we
    must fall through to recognition rather than extract from noise."""
    with patch(f'{ADAPTER}.TempFilePipeline') as pipeline_cls:
        pipeline_cls.return_value.arun = AsyncMock(return_value=_fake_pipeline_result('x'))
        with patch(f'{ADAPTER}.ParseAdapter._has_recognition_service', return_value=False):
            with pytest.raises(DocExtractNoParserError):
                await ParseAdapter.run_sync_doc_extract(
                    file_path=str(tmp_doc), file_name='license.txt', user_id=1
                )


async def test_empty_text_without_recognition_service_raises_11405(tmp_doc):
    with patch(f'{ADAPTER}.TempFilePipeline') as pipeline_cls:
        pipeline_cls.return_value.arun = AsyncMock(return_value=_fake_pipeline_result(''))
        with patch(f'{ADAPTER}.ParseAdapter._has_recognition_service', return_value=False):
            with pytest.raises(DocExtractNoParserError):
                await ParseAdapter.run_sync_doc_extract(
                    file_path=str(tmp_doc), file_name='license.txt', user_id=1
                )


async def test_threshold_boundary_is_inclusive(tmp_doc):
    """Exactly at the threshold counts as usable."""
    text = 'a' * MIN_USABLE_TEXT_LENGTH
    with patch(f'{ADAPTER}.TempFilePipeline') as pipeline_cls:
        pipeline_cls.return_value.arun = AsyncMock(return_value=_fake_pipeline_result(text))
        _, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert route == ROUTE_TEXT


async def test_one_below_threshold_is_not_usable(tmp_doc):
    text = 'a' * (MIN_USABLE_TEXT_LENGTH - 1)
    with patch(f'{ADAPTER}.TempFilePipeline') as pipeline_cls:
        pipeline_cls.return_value.arun = AsyncMock(return_value=_fake_pipeline_result(text))
        with patch(f'{ADAPTER}.ParseAdapter._has_recognition_service', return_value=False):
            with pytest.raises(DocExtractNoParserError):
                await ParseAdapter.run_sync_doc_extract(
                    file_path=str(tmp_doc), file_name='license.txt', user_id=1
                )


async def test_multiple_documents_are_joined(tmp_doc):
    class _Doc:
        def __init__(self, content):
            self.page_content = content

    class _Result:
        documents = [_Doc('part-one ' * 20), _Doc('part-two ' * 20)]

    with patch(f'{ADAPTER}.TempFilePipeline') as pipeline_cls:
        pipeline_cls.return_value.arun = AsyncMock(return_value=_Result())
        text, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )

    assert route == ROUTE_TEXT
    assert 'part-one' in text and 'part-two' in text


async def test_whitespace_only_pages_are_dropped(tmp_doc):
    class _Doc:
        def __init__(self, content):
            self.page_content = content

    class _Result:
        documents = [_Doc('   \n\n  '), _Doc('real content ' * 20)]

    with patch(f'{ADAPTER}.TempFilePipeline') as pipeline_cls:
        pipeline_cls.return_value.arun = AsyncMock(return_value=_Result())
        text, _ = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )

    assert text == ('real content ' * 20).strip() or text.startswith('real content')


def test_recognition_service_detection_delegates_to_knowledge_config():
    """Detection must come from the existing provider config, not a new setting
    (design §1 non-goal). It reuses `KnowledgeConf.image_parser_enabled`, which
    already mirrors the loader-selection logic in `base_file_pipeline.py` --
    duplicating that provider-matching here is what would let the two drift."""
    fake_conf = type('C', (), {'image_parser_enabled': True})()
    with patch(f'{ADAPTER}.settings') as mock_settings:
        mock_settings.get_knowledge.return_value = fake_conf
        assert ParseAdapter._has_recognition_service() is True

    fake_conf = type('C', (), {'image_parser_enabled': False})()
    with patch(f'{ADAPTER}.settings') as mock_settings:
        mock_settings.get_knowledge.return_value = fake_conf
        assert ParseAdapter._has_recognition_service() is False
