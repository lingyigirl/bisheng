"""F078 adapter selection by protocol."""

from __future__ import annotations

from bisheng.common.errcode.managed_agent import ManagedAgentProtocolUnsupportedError
from bisheng.managed_agent.domain.models.managed_agent import AgentProtocol
from bisheng.managed_agent.domain.services.adapters.custom_http import CustomHttpAdapter
from bisheng.managed_agent.domain.services.adapters.native import NativeAdapter
from bisheng.managed_agent.domain.services.adapters.openai_compatible import (
    OpenAICompatibleAdapter,
)


def get_adapter(agent):
    if agent.protocol == AgentProtocol.NATIVE.value:
        return NativeAdapter()
    if agent.protocol == AgentProtocol.OPENAI_COMPATIBLE.value:
        return OpenAICompatibleAdapter()
    if agent.protocol == AgentProtocol.CUSTOM_HTTP.value:
        return CustomHttpAdapter()
    raise ManagedAgentProtocolUnsupportedError(msg=f"unsupported protocol: {agent.protocol}")
