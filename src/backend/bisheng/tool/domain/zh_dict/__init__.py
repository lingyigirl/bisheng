"""MCP tool Chinese display dictionaries.

Vendors (e.g. cnfic) publish Chinese tool dictionaries on their websites while
their MCP servers return English ``tools/list`` descriptions. These snapshots
map a server URL prefix to per-tool Chinese ``desc`` and parameter descriptions,
applied in-place when a tool category is created or refreshed.
"""

import json
from pathlib import Path

_DICT_FILES = ("cnfic.json",)
_loaded: dict | None = None


def _load() -> dict:
    global _loaded
    if _loaded is None:
        merged: dict = {"servers": {}}
        base = Path(__file__).parent
        for fname in _DICT_FILES:
            with open(base / fname, encoding="utf-8") as f:
                data = json.load(f)
            merged["servers"].update(data.get("servers", {}))
        _loaded = merged
    return _loaded


def find_server_dict(server_url: str) -> dict | None:
    """Return the Chinese dictionary entry matching ``server_url``, if any."""
    for prefix, entry in _load()["servers"].items():
        if prefix and prefix in server_url:
            return entry
    return None


def apply_zh_dict(server_url: str, tools: list) -> None:
    """Overwrite tool descriptions and param descriptions with Chinese, in place.

    ``tools`` are ``mcp.types.Tool`` instances; both ``description`` and
    ``inputSchema.properties.*.description`` are mutated, so subsequent
    ``model_dump_json()`` / ``convert_input_schema`` calls pick up the
    Chinese text. Tools not present in the dictionary are left untouched.
    """
    entry = find_server_dict(server_url)
    if not entry:
        return
    tools_dict = entry.get("tools", {})
    for tool in tools:
        zh = tools_dict.get(tool.name)
        if not zh:
            continue
        if zh.get("desc"):
            tool.description = zh["desc"]
        params = zh.get("params") or {}
        schema = tool.inputSchema or {}
        properties = schema.get("properties") or {}
        for pname, pdesc in params.items():
            if pname in properties and pdesc:
                properties[pname]["description"] = pdesc
