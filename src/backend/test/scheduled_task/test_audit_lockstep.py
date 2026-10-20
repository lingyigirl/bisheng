"""F077 T-06/T-06a：审计白名单的**前后端锁步**守卫。

背景：`ainsert_v2` 不写 `system_id`，一条审计行能不能在审计页被看到，完全取决于
它的 action 是否在 `_UI_VISIBLE_V2_ACTIONS` 里——漏登记就是"行写进去了、谁也看不到"，
而且**不报任何错**。前端 `platform/src/controllers/API/log.ts` 是这份白名单的手工镜像
（模块下拉 + 动作下拉 + `getActionsByModuleApi` 的分支三处），两处不同步同样静默失效。

反面口径同样要守：`schedule.run`（逐轮运行留痕）**必须不在**白名单里——它由调度产生，
频率不受人控制（对齐 F076 `open_api.call` 的处置）。
"""

from pathlib import Path

from bisheng.database.models.audit_log import (
    _UI_VISIBLE_V2_ACTIONS,
    _V2_NAMESPACE_TO_ACTION_PREFIX,
)
from bisheng.scheduled_task.domain.constants import UI_VISIBLE_ACTIONS, ScheduledTaskAuditAction

REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_TS = REPO_ROOT / "frontend" / "platform" / "src" / "controllers" / "API" / "log.ts"


def test_management_actions_are_ui_visible():
    missing = [a for a in UI_VISIBLE_ACTIONS if a not in _UI_VISIBLE_V2_ACTIONS]
    assert not missing, f"audit rows would be written but unreachable: {missing}"


def test_run_action_is_not_ui_visible():
    """逐轮运行留痕只落库取证；进白名单会把审计页刷屏。"""
    assert ScheduledTaskAuditAction.RUN.value not in _UI_VISIBLE_V2_ACTIONS
    assert ScheduledTaskAuditAction.RUN.value not in UI_VISIBLE_ACTIONS


def test_schedule_namespace_is_mapped():
    """审计页的模块筛选把合成命名空间映射成 action 前缀；没有它新行按模块筛不出来。"""
    assert _V2_NAMESPACE_TO_ACTION_PREFIX.get("schedule") == "schedule."


def test_all_actions_share_the_namespace_prefix():
    for action in (*UI_VISIBLE_ACTIONS, ScheduledTaskAuditAction.RUN.value):
        assert action.startswith("schedule."), action


def test_frontend_mirror_lists_every_action():
    if not LOG_TS.exists():  # pragma: no cover - frontend not checked out
        return
    content = LOG_TS.read_text(encoding="utf-8")
    missing = [a for a in UI_VISIBLE_ACTIONS if f"value: '{a}'" not in content]
    assert not missing, (
        f"frontend log.ts is missing these actions, so the audit page filter will not offer them: {missing}"
    )
    # 模块下拉项：没有它，动作即使在列表里也选不到模块去筛。
    assert "{ name: 'log.systemIdEnum.schedule', value: 'schedule' }" in content
    # 模块 → 动作的联动分支：没有它，选中模块后动作下拉是空的。
    assert "case 'schedule': return actions.filter(a => a.value.startsWith('schedule.'))" in content
    # 逐轮运行留痕不得出现在前端筛选面。
    assert "value: 'schedule.run'" not in content


def test_frontend_i18n_keys_exist_in_all_three_languages():
    """模块名与六个动作名的 i18n 键三语齐备（键名 camelCase，避开 i18next 的 `.` 嵌套）。"""
    import json

    required = ["schedule"]
    # `schedule.task.run_now` -> `scheduleTaskRunNow`
    required += [
        "schedule" + "".join(part.capitalize() for chunk in a.split(".")[1:] for part in chunk.split("_"))
        for a in UI_VISIBLE_ACTIONS
    ]
    for lang in ("zh-Hans", "en-US", "ja"):
        path = REPO_ROOT / "frontend" / "platform" / "public" / "locales" / lang / "bs.json"
        if not path.exists():  # pragma: no cover - frontend not checked out
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key in required:
            group = "systemIdEnum" if key == "schedule" else "eventTypeEnum"
            assert key in data["log"][group], f"{lang} bs.json missing log.{group}.{key}"
