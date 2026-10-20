import unittest

from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

from bisheng.core.ai.llm.chat_openai_compatible import ChatOpenAICompatible
from bisheng.core.ai.llm.chat_openai_reasoning import ChatOpenAIReasoning


class TestChatOpenAIReasoning(unittest.TestCase):
    def test_openai_compatible_only_keeps_max_tokens_compat(self):
        llm = ChatOpenAICompatible(api_key="test", model="test-model")

        payload = llm._get_request_payload(
            [HumanMessage(content="question")],
            max_completion_tokens=123,
        )

        self.assertEqual(payload.get("max_tokens"), 123)
        self.assertNotIn("max_completion_tokens", payload)

    def test_create_chat_result_keeps_reasoning_content(self):
        llm = ChatOpenAIReasoning(api_key="test", model="test-model")

        result = llm._create_chat_result(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "answer",
                            "reasoning": "result reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "model": "test-model",
            }
        )

        self.assertEqual(
            result.generations[0].message.additional_kwargs.get("reasoning_content"),
            "result reasoning",
        )

    def test_create_chat_result_prefers_reasoning_content(self):
        llm = ChatOpenAIReasoning(api_key="test", model="test-model")

        result = llm._create_chat_result(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "answer",
                            "reasoning": "fallback reasoning",
                            "reasoning_content": "preferred reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "model": "test-model",
            }
        )

        self.assertEqual(
            result.generations[0].message.additional_kwargs.get("reasoning_content"),
            "preferred reasoning",
        )

    def test_convert_chunk_to_generation_chunk_keeps_reasoning_content(self):
        llm = ChatOpenAIReasoning(api_key="test", model="test-model")

        chunk = llm._convert_chunk_to_generation_chunk(
            {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "content": "partial answer",
                            "reasoning_content": "stream reasoning",
                        },
                        "finish_reason": None,
                    }
                ]
            },
            AIMessageChunk,
            {},
        )

        self.assertIsNotNone(chunk)
        self.assertEqual(
            chunk.message.additional_kwargs.get("reasoning_content"),
            "stream reasoning",
        )

    def test_convert_chunk_to_generation_chunk_falls_back_to_reasoning(self):
        llm = ChatOpenAIReasoning(api_key="test", model="test-model")

        chunk = llm._convert_chunk_to_generation_chunk(
            {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "content": "partial answer",
                            "reasoning": "fallback stream reasoning",
                        },
                        "finish_reason": None,
                    }
                ]
            },
            AIMessageChunk,
            {},
        )

        self.assertIsNotNone(chunk)
        self.assertEqual(
            chunk.message.additional_kwargs.get("reasoning_content"),
            "fallback stream reasoning",
        )

    def test_tool_image_is_moved_to_following_user_message(self):
        """OpenAI tool messages accept text parts only; images go in a user turn.

        The assistant/tool pairing and ``tool_call_id`` must survive the move.
        """
        llm = ChatOpenAIReasoning(api_key="test", model="test-model")
        message = ToolMessage(
            content_blocks=[
                {"type": "text", "text": "rendered chart"},
                {"type": "image", "base64": "AAAB", "mime_type": "image/png"},
            ],
            name="read_file",
            tool_call_id="call-1",
            status="success",
        )

        payload = llm._get_request_payload([message])

        self.assertEqual(payload["messages"][0]["role"], "tool")
        self.assertEqual(payload["messages"][0]["tool_call_id"], "call-1")
        self.assertEqual(
            payload["messages"][0]["content"],
            [{"type": "text", "text": "rendered chart"}],
        )
        image_message = payload["messages"][1]
        self.assertEqual(image_message["role"], "user")
        self.assertEqual(
            image_message["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAB"},
            },
        )

    def test_parallel_tool_images_share_one_user_message(self):
        llm = ChatOpenAIReasoning(api_key="test", model="test-model")
        messages = [
            ToolMessage(
                content_blocks=[
                    {"type": "image", "base64": "AAAB", "mime_type": "image/png"},
                ],
                name="read_file",
                tool_call_id="call-1",
                status="success",
            ),
            ToolMessage(
                content_blocks=[
                    {"type": "image", "base64": "BBBC", "mime_type": "image/jpeg"},
                ],
                name="read_file",
                tool_call_id="call-2",
                status="success",
            ),
        ]

        payload = llm._get_request_payload(messages)

        self.assertEqual(payload["messages"][0]["tool_call_id"], "call-1")
        self.assertEqual(payload["messages"][1]["tool_call_id"], "call-2")
        self.assertEqual(payload["messages"][2]["role"], "user")
        self.assertEqual(
            [block["image_url"]["url"] for block in payload["messages"][2]["content"] if block["type"] == "image_url"],
            [
                "data:image/png;base64,AAAB",
                "data:image/jpeg;base64,BBBC",
            ],
        )


if __name__ == "__main__":
    unittest.main()
