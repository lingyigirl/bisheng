"""DeepSeek chat adapter with BiSheng-specific payload fixes.

1. ``reasoning_content`` passthrough for deepseek-reasoner (existing behavior).
2. Real multimodal tool results: ``langchain_deepseek`` flattens ANY list
   content on a ``tool`` message into a JSON string for API compatibility
   (``chat_models.py``: ``json.dumps(message["content"])`` when the role is
   ``tool``). For an image returned by ``read_file`` that turns the picture
   into ~80k chars of base64 TEXT: the model cannot see it (and confabulates a
   description) while the request is billed at text rates. Measured against
   api.deepseek.com (2026-10-15): one 62KB slide costs 797 prompt tokens as a
   real ``image_url`` block vs 53,054 as the flattened string. The API accepts
   real block lists in tool messages, so the list is restored here whenever
   the original message actually carried one (see ``_restore_dumped_tool_list``).
"""

import json
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_deepseek import ChatDeepSeek

# Block types verified accepted by api.deepseek.com inside a TOOL message's
# content list (2026-10-15 probe): text-only, image-only, mixed text+image and
# multi-image all return normal completions. Anything else (file / audio /
# video — shapes no OpenAI-compatible endpoint takes) keeps upstream's string.
_TOOL_LIST_SAFE_BLOCK_TYPES = frozenset({"text", "image_url"})


def _restore_dumped_tool_list(payload_msg: dict, message: BaseMessage) -> None:
    """Undo langchain-deepseek's ``json.dumps`` of list tool content.

    Only fires when BOTH sides agree the message was a list: the LC message's
    ``content`` is a list (the exact predicate upstream dumps on) and the
    payload holds a string that parses back into a list of shapes the API is
    known to accept. A plain-string tool result (the common case, e.g. the
    code interpreter's JSON output) is never touched.
    """
    if payload_msg.get("role") != "tool":
        return
    if not isinstance(getattr(message, "content", None), list):
        return
    dumped = payload_msg.get("content")
    if not isinstance(dumped, str):
        return
    try:
        blocks = json.loads(dumped)
    except json.JSONDecodeError:
        return
    if not isinstance(blocks, list) or not blocks:
        return
    if not all(
        isinstance(block, dict) and block.get("type") in _TOOL_LIST_SAFE_BLOCK_TYPES
        for block in blocks
    ):
        return
    payload_msg["content"] = blocks


class CustomChatDeepSeek(ChatDeepSeek):

    def _get_request_payload(
            self,
            input_: LanguageModelInput,
            *,
            stop: list[str] | None = None,
            **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = self._convert_input(input_).to_messages()

        for payload_msg, msg in zip(payload['messages'], messages, strict=False):
            if isinstance(msg, AIMessage):
                payload_msg['reasoning_content'] = msg.additional_kwargs.get('reasoning_content', '')
            # Azure-hosted DeepSeek deployments are not verified to accept
            # block lists on tool messages — keep upstream's flattening there.
            if not self._is_azure_endpoint:
                _restore_dumped_tool_list(payload_msg, msg)

        return payload
