"""F077:权限位 code ↔ 三语文案锁步。

前端把权限位 code 翻成文案用的是**本地映射**（`KeyIssueDialog.tsx` 的
`scopeLocaleKey`：`scope.` + `code.replace(':', '_')`）。运行期注册表
（`open_api/domain/scopes.py`）与三个语言文件因此是双份手维护——新增权限位而漏配
文案时，界面会显示原始 i18n key（如 `scope.schedule_trigger`）而不是标签，**静默且
难查**。F077 新增 `schedule:trigger` 时正是这样漏了一次，本测试防复发。

先例：`test/doc_extract/test_audit_lockstep.py` 直接读前端文件做锁步断言。
"""

import json
from pathlib import Path

from bisheng.open_api.domain.scopes import OPEN_API_SCOPES

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCALES = ("zh-Hans", "en-US", "ja")


def _locale_scopes(lang: str) -> dict:
    path = REPO_ROOT / "frontend" / "platform" / "public" / "locales" / lang / "serviceAccount.json"
    if not path.exists():  # pragma: no cover - frontend not checked out
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("scope", {})


def test_every_registered_scope_has_a_label_in_all_locales():
    for lang in LOCALES:
        scopes = _locale_scopes(lang)
        if not scopes:  # pragma: no cover - frontend not checked out
            continue
        missing = [item.code for item in OPEN_API_SCOPES if item.code.replace(":", "_") not in scopes]
        assert not missing, (
            f"{lang}/serviceAccount.json 缺少权限位文案 {missing}——"
            f"界面会把 code 原样渲染成 scope.<key>（见 KeyIssueDialog.scopeLocaleKey）"
        )


def test_no_locale_label_outlives_the_registry():
    """反向：语言文件里不得残留注册表已不存在的权限位（防改名后旧键堆积）。"""
    registered = {item.code.replace(":", "_") for item in OPEN_API_SCOPES}
    for lang in LOCALES:
        scopes = _locale_scopes(lang)
        if not scopes:  # pragma: no cover - frontend not checked out
            continue
        stale = sorted(set(scopes) - registered)
        assert not stale, f"{lang}/serviceAccount.json 存在注册表外的权限位文案: {stale}"
