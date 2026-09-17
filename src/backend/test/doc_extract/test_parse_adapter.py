"""Parsing: route reporting and the "no usable content" failure.

Route is derived from the loader the pipeline actually selected rather than
guessed from the file extension -- a deployment with a recognition provider
hands images and scanned PDFs to it, and the operator needs to see that.

Covers spec AC-21 (both input kinds accepted), AC-22 (a document with a text
layer is used directly) and AC-23 (a document without one goes through the
configured recognition service).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from bisheng.common.errcode.doc_extract import (
    DocExtractContentFetchError,
    DocExtractNoParserError,
)
from bisheng.doc_extract.domain.services.parse_adapter import (
    MIN_USABLE_TEXT_LENGTH,
    ROUTE_OCR,
    ROUTE_TEXT,
    ParseAdapter,
)

ADAPTER = 'bisheng.doc_extract.domain.services.parse_adapter'


class _Pipeline:
    """Stands in for TempFilePipeline, including the `loader` attribute the real
    pipeline sets during `run()`."""

    def __init__(self, text, loader_name='LocalPdfLoader'):
        self._text = text
        self.loader = type(loader_name, (), {})() if loader_name else None

    async def arun(self):
        class _Doc:
            def __init__(self, content):
                self.page_content = content

        class _Result:
            def __init__(self, content):
                self.documents = [_Doc(content)] if content else []

        return _Result(self._text)


@pytest.fixture()
def tmp_doc(tmp_path: Path) -> Path:
    path = tmp_path / 'license.txt'
    path.write_text('营业执照正文内容', encoding='utf-8')
    return path


def _patch_pipeline(text, loader_name='LocalPdfLoader'):
    return patch(f'{ADAPTER}.TempFilePipeline', return_value=_Pipeline(text, loader_name))


# --- route reporting --------------------------------------------------------


async def test_local_loader_reports_text_route(tmp_doc):
    long_text = '营业执照内容' * 20
    with _patch_pipeline(long_text, 'LocalPdfLoader'):
        text, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert route == ROUTE_TEXT
    assert text == long_text


async def test_mineru_loader_reports_ocr_route(tmp_doc):
    """The route must reflect that an external service did the reading."""
    with _patch_pipeline('识别出的内容' * 10, 'MineruLoader'):
        _, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert route == ROUTE_OCR


async def test_paddle_ocr_loader_reports_ocr_route(tmp_doc):
    with _patch_pipeline('识别出的内容' * 10, 'PaddleOcrLoader'):
        _, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert route == ROUTE_OCR


async def test_etl4lm_loader_reports_ocr_route(tmp_doc):
    with _patch_pipeline('识别出的内容' * 10, 'Etl4lmLoader'):
        _, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert route == ROUTE_OCR


# --- the usability floor ----------------------------------------------------


async def test_short_ocr_result_is_accepted(tmp_doc):
    """Regression: a real 91-character license image was rejected by an earlier
    100-character floor and reported as "no content". A short result is not a
    failed result."""
    short = 'Business License No. 91450100MA5K Company: Guangxi Test Co Ltd'
    with _patch_pipeline(short, 'MineruLoader'):
        text, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert route == ROUTE_OCR
    assert text == short


async def test_text_exactly_at_floor_is_accepted(tmp_doc):
    text = 'a' * MIN_USABLE_TEXT_LENGTH
    with _patch_pipeline(text):
        _, route = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert route == ROUTE_TEXT


async def test_empty_result_without_recognition_service_raises_11605(tmp_doc):
    with (
        _patch_pipeline('', 'LocalPdfLoader'),
        patch(f'{ADAPTER}.ParseAdapter._has_recognition_service', return_value=False),
    ):
        with pytest.raises(DocExtractNoParserError):
            await ParseAdapter.run_sync_doc_extract(
                file_path=str(tmp_doc), file_name='license.txt', user_id=1
            )


async def test_empty_result_from_recognition_service_raises_11608(tmp_doc):
    """Spec §3.1 row 9, not row 4.

    "Configured but produced nothing" is a *different* failure from "no service
    exists", and it carries a different code on purpose: 11605 tells the
    operator to configure a recognition service, which is actively misleading
    when one is already configured and simply failed. Regression guard -- this
    previously raised 11605, sending operators to check a setting that was fine.
    """
    with (
        _patch_pipeline('   \n  ', 'MineruLoader'),
        patch(f'{ADAPTER}.ParseAdapter._has_recognition_service', return_value=True),
    ):
        with pytest.raises(DocExtractContentFetchError) as exc:
            await ParseAdapter.run_sync_doc_extract(
                file_path=str(tmp_doc), file_name='license.txt', user_id=1
            )
    assert '已配置识别服务' in str(exc.value)


async def test_parse_exception_with_service_configured_raises_11608(tmp_doc):
    """An exception from a *configured* service is a service failure (row 9),
    not "no parser" (row 4)."""
    with (
        patch(f'{ADAPTER}.TempFilePipeline', side_effect=RuntimeError('mineru unreachable')),
        patch(f'{ADAPTER}.ParseAdapter._has_recognition_service', return_value=True),
    ):
        with pytest.raises(DocExtractContentFetchError):
            await ParseAdapter.run_sync_doc_extract(
                file_path=str(tmp_doc), file_name='license.txt', user_id=1
            )


# --- pipeline failure handling ----------------------------------------------


async def test_parse_exception_falls_through_to_no_parser(tmp_doc):
    """A parse failure is the scanned-document case, not an immediate error --
    it must reach the route decision rather than propagate."""
    with (
        patch(f'{ADAPTER}.TempFilePipeline', side_effect=RuntimeError('parse blew up')),
        patch(f'{ADAPTER}.ParseAdapter._has_recognition_service', return_value=False),
    ):
        with pytest.raises(DocExtractNoParserError):
            await ParseAdapter.run_sync_doc_extract(
                file_path=str(tmp_doc), file_name='license.txt', user_id=1
            )


async def test_multiple_documents_are_joined(tmp_doc):
    class _Doc:
        def __init__(self, content):
            self.page_content = content

    class _Result:
        documents = [_Doc('part-one ' * 5), _Doc('part-two ' * 5)]

    class _PipelineMulti:
        loader = None

        async def arun(self):
            return _Result()

    with patch(f'{ADAPTER}.TempFilePipeline', return_value=_PipelineMulti()):
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
        documents = [_Doc('   \n\n  '), _Doc('real content ' * 5)]

    class _PipelineWs:
        loader = None

        async def arun(self):
            return _Result()

    with patch(f'{ADAPTER}.TempFilePipeline', return_value=_PipelineWs()):
        text, _ = await ParseAdapter.run_sync_doc_extract(
            file_path=str(tmp_doc), file_name='license.txt', user_id=1
        )
    assert text.startswith('real content')


# --- recognition service detection ------------------------------------------


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


def test_route_for_unknown_loader_defaults_to_text():
    """An unrecognised loader name must not be reported as OCR -- that would
    point a debugger at an external service that was never involved."""
    assert ParseAdapter._route_for('SomeFutureLoader') == ROUTE_TEXT
    assert ParseAdapter._route_for('') == ROUTE_TEXT
