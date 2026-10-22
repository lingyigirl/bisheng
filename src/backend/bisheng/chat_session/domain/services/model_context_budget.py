"""模型上下文窗口与历史 Token 预算。"""

from loguru import logger

from bisheng.llm.domain.models import LLMDao

DEFAULT_MODEL_CONTEXT_WINDOW = 8192
_CONTEXT_KEYS = (
    "context_window",
    "context_length",
    "max_context_tokens",
    "max_input_tokens",
)


def resolve_model_context_window(model_id: int | str | None) -> int:
    try:
        model = LLMDao.get_model_by_id(int(model_id)) if model_id is not None else None
    except Exception as exc:
        logger.warning("read model context window failed model_id={} error={}", model_id, exc)
        model = None
    config = getattr(model, "config", None) or {}
    for key in _CONTEXT_KEYS:
        try:
            value = int(config.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    logger.warning(
        "model context window missing model_id={} fallback={}",
        model_id,
        DEFAULT_MODEL_CONTEXT_WINDOW,
    )
    return DEFAULT_MODEL_CONTEXT_WINDOW


def calculate_history_budget(
    *,
    context_window: int,
    fixed_prompt_tokens: int,
    output_reserve: int,
    safety_reserve: int,
) -> int:
    return max(0, context_window - fixed_prompt_tokens - output_reserve - safety_reserve)


def get_cached_model_budget(cache: dict[str, dict[str, int]], model_id: int | str | None) -> dict[str, int]:
    """同一次工作流运行内复用模型预算，模型变更在下一次运行生效。"""
    cache_key = str(model_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    context_window = resolve_model_context_window(model_id)
    budget = {
        "context_window": context_window,
        "output_reserve": min(2048, max(512, context_window // 10)),
        "safety_reserve": max(256, context_window // 20),
    }
    cache[cache_key] = budget
    return budget
