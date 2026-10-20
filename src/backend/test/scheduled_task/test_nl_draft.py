"""F077 T-20/T-21：自然语言草稿与"应用可定时化"形状校验。

草稿链路：模型调用打桩，锁的是**服务端硬校验**——模型说错（坏的 cron / 过去的时间 /
超尺寸输入 / 非 JSON）一律 11915，且**不落库**（本模块没有写库路径）。
形状校验：`input_nodes` / `describe_unschedulable` 是纯函数，直接喂 `flow.data` 结构。
"""

import json
from datetime import datetime, timedelta

import pytest

from bisheng.common.errcode.scheduled_task import (
    ScheduledTaskAppNotSchedulableError,
    ScheduledTaskNlDraftInvalidError,
)
from bisheng.database.models.flow import FlowType
from bisheng.scheduled_task.domain.services import app_shape_guard, nl_draft_service

TENANT_ID = 7


# ── 形状校验（纯函数）────────────────────────────────────────────────


def _node(node_type="input", params=None):
    return {"id": "n", "type": node_type, "data": {"group_params": [{"params": params or []}]}}


def _form_node(fields):
    return _node(params=[{"key": "tab", "value": "form_input"}, {"key": "form_input", "value": fields}])


def test_input_nodes_and_reasons():
    assert app_shape_guard.input_nodes({}) == []

    single = app_shape_guard.input_nodes({"nodes": [_form_node([{"key": "a", "type": "input"}])]})
    assert len(single) == 1 and single[0]["tab"] == "form_input"

    two = {"nodes": [_form_node([]), _form_node([])]}
    assert len(app_shape_guard.input_nodes(two)) == 2
    assert "multiple input nodes" in app_shape_guard.describe_unschedulable(two)

    with_file = {"nodes": [_form_node([{"key": "f", "type": "file"}])]}
    assert "file input" in app_shape_guard.describe_unschedulable(with_file)

    ok = {"nodes": [_form_node([{"key": "a", "type": "input"}]), _node("llm")]}
    assert app_shape_guard.describe_unschedulable(ok) == ""


async def test_assert_app_schedulable(monkeypatch):
    state = {"data": {"nodes": [_form_node([{"key": "a", "type": "input"}])]}, "lookups": 0}

    class _Flow:
        def __init__(self, data):
            self.data = data

    async def _aget(flow_id):
        state["lookups"] += 1
        return _Flow(state["data"])

    monkeypatch.setattr(app_shape_guard.FlowDao, "aget_flow_by_id", _aget)

    # 助手恒通过，且不查 flow 表
    await app_shape_guard.assert_app_schedulable("a1", FlowType.ASSISTANT.value)
    assert state["lookups"] == 0

    # 单输入 + 非文件 → 通过
    await app_shape_guard.assert_app_schedulable("1001", FlowType.WORKFLOW.value)

    # 文件类 → 11914
    state["data"] = {"nodes": [_form_node([{"key": "f", "type": "file"}])]}
    with pytest.raises(ScheduledTaskAppNotSchedulableError) as excinfo:
        await app_shape_guard.assert_app_schedulable("1001", FlowType.WORKFLOW.value)
    assert excinfo.value.Code == 11914


# ── 草稿链路 ─────────────────────────────────────────────────────────


class _Reply:
    def __init__(self, content):
        self.content = content


class _Model:
    def __init__(self, content):
        self.content = content

    async def ainvoke(self, prompt):
        return _Reply(self.content)


@pytest.fixture()
def model(monkeypatch):
    state = {"content": "{}"}

    async def _resolve(tenant_id, user_id):
        return _Model(state["content"])

    monkeypatch.setattr(nl_draft_service, "_resolve_model", _resolve)
    return state


async def test_cron_draft_returns_normalized_plan(model):
    model["content"] = json.dumps(
        {
            "schedule_type": "cron",
            "cron_expression": "0   9 * * 1-5",
            "run_at": None,
            "preset_inputs": [{"key": "客户名称", "value": "甲公司"}],
            "rationale": "工作日每天九点",
        }
    )
    draft = await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="每个工作日早上九点跑一次")
    assert draft["cron_expression"] == "0 9 * * 1-5"  # 归一化
    assert "9:00" in draft["schedule_description"]
    assert draft["schedule_display"]["day"] == {
        "kind": "weekdays",
        "weekdays": [1, 2, 3, 4, 5],
        "range": True,
    }
    assert draft["schedule_display"]["time"] == {"kind": "at", "times": [{"hour": 9, "minute": 0}]}
    assert len(draft["next_runs"]) == 5
    assert draft["preset_inputs"] == [{"key": "客户名称", "value": "甲公司"}]
    assert draft["rationale"] == "工作日每天九点"


async def test_once_draft_parses_absolute_time(model):
    run_at = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    model["content"] = json.dumps({"schedule_type": "once", "run_at": run_at, "rationale": "一次"})
    draft = await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="后天跑一次")
    assert draft["schedule_type"] == "once"
    assert draft["run_at"].strftime("%Y-%m-%d %H:%M") == run_at
    assert draft["schedule_display"] == {
        "day": None,
        "time": {"kind": "once", "run_at": draft["run_at"].isoformat()},
    }
    assert draft["next_runs"] == [draft["run_at"]]


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps([1, 2, 3]),
        json.dumps({"schedule_type": "cron", "cron_expression": "@daily"}),
        json.dumps({"schedule_type": "cron"}),
        json.dumps({"schedule_type": "once"}),
        json.dumps({"schedule_type": "once", "run_at": "2000-01-01 09:00"}),
        json.dumps({"schedule_type": "cron", "cron_expression": "0 9 * * *", "preset_inputs": [{"value": "x"}]}),
    ],
)
async def test_invalid_model_output_is_rejected(model, content):
    model["content"] = content
    with pytest.raises(ScheduledTaskNlDraftInvalidError) as excinfo:
        await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="随便")
    assert excinfo.value.Code == 11915


async def test_repairs_fenced_json(model):
    model["content"] = '```json\n{"schedule_type": "cron", "cron_expression": "0 9 * * *"}\n```'
    draft = await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="每天九点")
    assert draft["cron_expression"] == "0 9 * * *"


async def test_input_limits(model):
    model["content"] = json.dumps({"schedule_type": "cron", "cron_expression": "0 9 * * *"})
    with pytest.raises(ScheduledTaskNlDraftInvalidError):
        await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="   ")
    with pytest.raises(ScheduledTaskNlDraftInvalidError):
        await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="x" * 2001)


async def test_no_model_available(monkeypatch):
    async def _none(tenant_id, user_id):
        return None

    monkeypatch.setattr(nl_draft_service, "_resolve_model", _none)
    with pytest.raises(ScheduledTaskNlDraftInvalidError):
        await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="每天九点")


async def test_draft_does_not_touch_the_database(model, monkeypatch):
    """AC-42 的核心：草稿阶段不产生任何持久副作用。"""
    from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao

    async def _boom(*args, **kwargs):
        raise AssertionError("draft must not write to the database")

    monkeypatch.setattr(ScheduledTaskDao, "acreate", _boom)
    monkeypatch.setattr(ScheduledTaskDao, "aupdate_fields", _boom)

    model["content"] = json.dumps({"schedule_type": "cron", "cron_expression": "0 9 * * *"})
    await nl_draft_service.draft_from_text(tenant_id=TENANT_ID, user_id=7, text="每天九点")
