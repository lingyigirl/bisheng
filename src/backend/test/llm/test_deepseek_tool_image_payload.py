"""``langchain_deepseek`` flattens list content on tool messages to a JSON
string; for image reads that silently turns the picture into ~80k chars of
base64 text (billed at text rates, invisible to the model). 2026-10-15
measurement against api.deepseek.com for one 62KB slide:

    real image_url block   ->    797 prompt tokens (model describes it correctly)
    json.dumps(list)       -> 53,054 prompt tokens (model cannot see it)

``CustomChatDeepSeek`` restores the list when the LC message actually carried
one. These tests pin that restoration and its boundaries.
"""

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from bisheng.core.ai.llm.custom_chat_deepseek import CustomChatDeepSeek


def _llm(base_url: str = "https://api.deepseek.com/v1") -> CustomChatDeepSeek:
    return CustomChatDeepSeek(model="deepseek-chat", api_key="test-key", base_url=base_url)


def _image_tool_message(mime: str = "image/jpeg", payload: str = "AAAB") -> ToolMessage:
    return ToolMessage(
        content_blocks=[{"type": "image", "base64": payload, "mime_type": mime}],
        name="read_file",
        tool_call_id="c1",
        status="success",
    )


class TestDeepSeekToolImagePayload(unittest.TestCase):
    def test_image_tool_result_goes_out_as_real_image_block(self):
        llm = _llm()

        payload = llm._get_request_payload([_image_tool_message()])

        self.assertEqual(
            payload["messages"][0]["content"],
            [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAB"}}],
        )

    def test_mixed_text_and_image_blocks_are_restored(self):
        llm = _llm()
        message = ToolMessage(
            content_blocks=[
                {"type": "text", "text": "slide 1 rendered"},
                {"type": "image", "base64": "AAAB", "mime_type": "image/png"},
            ],
            name="read_file",
            tool_call_id="c1",
            status="success",
        )

        payload = llm._get_request_payload([message])

        self.assertEqual(
            payload["messages"][0]["content"],
            [
                {"type": "text", "text": "slide 1 rendered"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAB"}},
            ],
        )

    def test_plain_string_tool_result_is_untouched(self):
        llm = _llm()
        message = ToolMessage(
            content='{"results": [], "stdout": ["ok"]}',
            name="bisheng_code_interpreter",
            tool_call_id="c2",
            status="success",
        )

        payload = llm._get_request_payload([message])

        self.assertEqual(payload["messages"][0]["content"], '{"results": [], "stdout": ["ok"]}')

    def test_unverified_block_types_keep_upstream_flattening(self):
        """A `file` block must not ride along just because the list parses —
        providers reject it, so upstream's (harmless) string stays."""
        llm = _llm()
        message = ToolMessage(
            content_blocks=[
                {"type": "file", "base64": "AAAB", "mime_type": "application/pdf"},
            ],
            name="read_file",
            tool_call_id="c3",
            status="success",
        )

        payload = llm._get_request_payload([message])

        self.assertIsInstance(payload["messages"][0]["content"], str)

    def test_azure_endpoint_keeps_upstream_flattening(self):
        llm = _llm(base_url="https://my-deployment.azure.com/v1")

        payload = llm._get_request_payload([_image_tool_message()])

        self.assertIsInstance(payload["messages"][0]["content"], str)

    def test_reasoning_content_still_propagates(self):
        llm = _llm()
        assistant = AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "thinking..."},
        )

        payload = llm._get_request_payload([HumanMessage(content="hi"), assistant])

        self.assertEqual(payload["messages"][1]["reasoning_content"], "thinking...")
