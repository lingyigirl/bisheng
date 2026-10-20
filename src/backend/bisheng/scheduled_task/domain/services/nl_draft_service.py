"""F077 自然语言 → 定时任务草稿（spec AC-41/AC-42/AC-43）。

**草稿不落库**：这个服务只调模型、只做服务端硬校验，然后返回一份可编辑的草稿；
落库发生在用户确认之后（走普通的 create 路径，AC-42）。模型输出是**不可信输入**：

- 解析用 ``json_repair`` 兜底（仓内既有依赖，先例 ``workflow/nodes/input/input.py``）；
- cron 是否合法、一次性时间能否解析、预设输入是否超尺寸，全部在服务端复核，
  任何不合法一律 11915，**不返回残缺草稿**（残缺草稿会诱使用户确认一个跑不起来的任务）。

一期只支持"新建"（AC-43）：修改/启停/删除既有任务仍走表单。
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from bisheng.common.errcode.managed_agent import ManagedAgentConfigInvalidError
from bisheng.common.errcode.scheduled_task import ScheduledTaskNlDraftInvalidError
from bisheng.common.models.config import ConfigKeyEnum
from bisheng.common.services.config_service import settings
from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE
from bisheng.scheduled_task.domain.models.scheduled_task import ScheduleType
from bisheng.scheduled_task.domain.services import run_service
from bisheng.scheduled_task.domain.services.cron_schedule import (
    describe,
    describe_parts,
    next_runs,
    normalize,
    validate_and_parse,
)

_PROMPT = """你是定时任务配置助手。根据用户的自然语言描述，输出一个 JSON 对象，不要输出任何其它内容。

JSON 结构：
{{"schedule_type": "cron" 或 "once", "cron_expression": "5 段 cron（分 时 日 月 周）或 null", "run_at": "YYYY-MM-DD HH:MM（一次性任务）或 null", "preset_inputs": [{{"key": "输入项", "value": "值"}}] 或 null, "rationale": "一句话解释"}}

规则：
- cron 只接受 5 段表达式（分钟粒度），例如「每天 9 点」= "0 9 * * *"；不支持 @daily 等别名；
- 「跑一次 / 某天某时」用 once，并把 run_at 写成服务器本地时间（Asia/Shanghai）；
- 无法确定时把对应字段置 null，并在 rationale 说明，不要编造；
- preset_inputs 只在描述里明确给出了固定输入值时才填。

当前服务器时间：{now}
用户描述：{text}
"""


def _conf():
    return settings.scheduled_task


async def _resolve_model(tenant_id: int, user_id: int):
    """NL 槽位优先，未配置/构建失败时回退租户默认对话模型（与 F061 同口径）。"""
    from bisheng.llm.domain.services.llm import LLMService
    from bisheng.llm.domain.services.model_slot import resolve_slot_model_id

    def _build(target_id: int):
        return LLMService.get_bisheng_llm_sync(
            model_id=target_id,
            app_id="scheduled_task",
            app_name="scheduled_task",
            app_type="scheduled_task",
            user_id=user_id,
        )

    model_id = resolve_slot_model_id(tenant_id, ConfigKeyEnum.SCHEDULED_TASK_LLM.value)
    if model_id is not None:
        try:
            return _build(model_id)
        except Exception:
            logger.opt(exception=True).warning(
                "scheduled_task: NL slot model {} could not be built; falling back", model_id
            )

    fallback_id = await LLMService.aget_default_conversation_model_id(tenant_id)
    if fallback_id is None or fallback_id == model_id:
        return None
    try:
        return _build(fallback_id)
    except Exception:
        logger.opt(exception=True).warning("scheduled_task: NL fallback model could not be built")
        return None


def _parse_draft(raw: str) -> dict:
    """容错解析模型输出（json_repair 兜底）；非 JSON 对象即 11915。"""
    import json

    text = (raw or "").strip()
    if not text:
        raise ScheduledTaskNlDraftInvalidError(exception="empty model output")
    try:
        payload = json.loads(text)
    except Exception:
        try:
            from json_repair import loads as repair_loads

            payload = repair_loads(text, skip_json_loads=True)
        except Exception:
            raise ScheduledTaskNlDraftInvalidError(exception="model output is not JSON")
    if not isinstance(payload, dict):
        raise ScheduledTaskNlDraftInvalidError(exception="model output is not a JSON object")
    return payload


def _parse_run_at(value) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ScheduledTaskNlDraftInvalidError(exception="run_at missing")
    text = value.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        raise ScheduledTaskNlDraftInvalidError(exception=f"run_at unparsable: {value}")


def _validate_presets(raw) -> list[dict] | None:
    if raw in (None, [], {}):
        return None
    if not isinstance(raw, list):
        raise ScheduledTaskNlDraftInvalidError(exception="preset_inputs must be a list")
    conf = _conf()
    if len(raw) > conf.preset_input_max_items:
        raise ScheduledTaskNlDraftInvalidError(exception="too many preset inputs")
    presets = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("key"):
            raise ScheduledTaskNlDraftInvalidError(exception="preset input missing key")
        value = item.get("value", "")
        if len(str(value)) > conf.preset_input_max_chars:
            raise ScheduledTaskNlDraftInvalidError(exception="preset input too long")
        presets.append({"key": str(item["key"]), "value": str(value)})
    return presets or None


async def _validate_managed_presets(app_id: str | None, app_type: int | None, presets):
    """F078: validate draft presets against a managed agent's Agent Card."""
    if app_type != MANAGED_AGENT_APP_TYPE or not app_id:
        return presets
    from bisheng.managed_agent.domain.models.dao import ManagedAgentDao
    from bisheng.managed_agent.domain.services.agent_card import (
        AgentCardFileInput,
        parse_agent_card,
        validate_preset_inputs,
    )

    agent = await ManagedAgentDao.aget(int(app_id))
    if agent is None:
        raise ScheduledTaskNlDraftInvalidError(exception="managed agent not found")
    if agent.source_kind == "native":
        return presets
    card = parse_agent_card(agent.agent_card)
    try:
        normalized = validate_preset_inputs(card, presets)
    except AgentCardFileInput as exc:
        raise ScheduledTaskNlDraftInvalidError(exception=f"file input is not schedulable: {exc}") from exc
    except ManagedAgentConfigInvalidError as exc:
        raise ScheduledTaskNlDraftInvalidError(exception=str(exc)[:200]) from exc
    return [{"key": item.key, "value": item.value} for item in normalized] or None


async def draft_from_text(
    *,
    tenant_id: int,
    user_id: int,
    text: str,
    app_id: str | None = None,
    app_type: int | None = None,
) -> dict:
    """把一句自然语言变成可编辑的草稿；**不落库**（AC-42）。"""
    conf = _conf()
    text = (text or "").strip()
    if not text:
        raise ScheduledTaskNlDraftInvalidError(exception="empty description")
    if len(text) > conf.nl_max_chars:
        raise ScheduledTaskNlDraftInvalidError(exception=f"description longer than {conf.nl_max_chars}")

    model = await _resolve_model(tenant_id, user_id)
    if model is None:
        raise ScheduledTaskNlDraftInvalidError(exception="no model available for the NL slot")

    now = datetime.now(run_service.SERVER_TZ).replace(tzinfo=None)
    try:
        reply = await model.ainvoke(_PROMPT.format(now=now.strftime("%Y-%m-%d %H:%M"), text=text))
    except Exception:
        logger.exception("scheduled_task: NL model call failed")
        raise ScheduledTaskNlDraftInvalidError(exception="model call failed")

    payload = _parse_draft(getattr(reply, "content", None) or str(reply))

    schedule_type = payload.get("schedule_type") or ScheduleType.CRON.value
    if schedule_type not in (ScheduleType.CRON.value, ScheduleType.ONCE.value):
        raise ScheduledTaskNlDraftInvalidError(exception="unsupported schedule_type")

    if schedule_type == ScheduleType.CRON.value:
        expression = payload.get("cron_expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ScheduledTaskNlDraftInvalidError(exception="cron_expression missing")
        try:
            validate_and_parse(expression)
        except Exception as exc:
            raise ScheduledTaskNlDraftInvalidError(exception=f"invalid cron: {exc}")
        normalized = normalize(expression)
        anchor = now.replace(tzinfo=run_service.SERVER_TZ)
        return {
            "schedule_type": schedule_type,
            "cron_expression": normalized,
            "run_at": None,
            "schedule_description": describe(normalized),
            "schedule_display": describe_parts(normalized),
            "next_runs": next_runs(normalized, anchor, conf.preview_count),
            "preset_inputs": await _validate_managed_presets(
                app_id, app_type, _validate_presets(payload.get("preset_inputs"))
            ),
            "rationale": str(payload.get("rationale") or ""),
        }

    run_at = _parse_run_at(payload.get("run_at"))
    if run_at <= now:
        raise ScheduledTaskNlDraftInvalidError(exception="run_at is in the past")
    return {
        "schedule_type": schedule_type,
        "cron_expression": None,
        "run_at": run_at,
        "schedule_description": run_at.strftime("%Y-%m-%d %H:%M"),
        "schedule_display": {"day": None, "time": {"kind": "once", "run_at": run_at.isoformat()}},
        "next_runs": [run_at],
        "preset_inputs": await _validate_managed_presets(
            app_id, app_type, _validate_presets(payload.get("preset_inputs"))
        ),
        "rationale": str(payload.get("rationale") or ""),
    }
