"""Project-owned fallback budget for DeepAgents conversation compaction."""

from deepagents._models import get_model_identifier, get_model_provider
from deepagents.middleware.summarization import SummarizationMiddleware, compute_summarization_defaults
from deepagents.profiles import HarnessProfile, register_harness_profile
from langchain_core.language_models import BaseChatModel

DEFAULT_COMPACTION_TOKENS = 200_000


class LinsightSummarizationMiddleware(SummarizationMiddleware):
    """Keep a distinct name so the SDK's default instance can be excluded."""


def build_context_compaction(model: BaseChatModel, backend) -> LinsightSummarizationMiddleware:
    """Replace the SDK instance without changing model capacity or SDK files."""
    provider = get_model_provider(model)
    identifier = get_model_identifier(model)
    if identifier and ":" in identifier:
        key = identifier
    elif provider:
        key = f"{provider}:{identifier}" if identifier else provider
    else:
        raise ValueError("Context compaction requires an identifiable model provider")
    # Registration merges with existing prompt/tool profiles. Each graph gets
    # its own middleware below; no mutable instance is shared through the registry.
    register_harness_profile(key, HarnessProfile(excluded_middleware={"SummarizationMiddleware"}))
    defaults = compute_summarization_defaults(model)
    trigger = defaults["trigger"]
    if trigger[0] == "tokens":
        trigger = ("tokens", DEFAULT_COMPACTION_TOKENS)
    return LinsightSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=trigger,
        keep=defaults["keep"],
        trim_tokens_to_summarize=None,
        truncate_args_settings=defaults["truncate_args_settings"],
    )
