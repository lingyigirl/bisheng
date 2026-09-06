"""F046 ops: seed the agent-monitoring dataset + dashboard components (idempotent).

Usage (from src/backend, env sourced):
    .venv/bin/python scripts/ops_seed_agent_monitoring.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select  # noqa: E402

from bisheng.core.database.manager import get_sync_db_session  # noqa: E402
from bisheng.telemetry_search.domain.models.dashboard import (  # noqa: E402
    Dashboard,
    DashboardComponent,
)
from bisheng.telemetry_search.domain.models.dashboard_dataset import (  # noqa: E402
    DashboardDataset,
)
from bisheng.telemetry_search.domain.models.dashboard_dataset import (  # noqa: E402
    SchemaConfig,
)

DASHBOARD_ID = 10
QUERY_COMPONENT_ID = "94408421c2514745b7eeeb4ed007ff8d"
DATASET_CODE = "mid_agent_run_dtl"

_STYLE = {
    "title": "", "bgColor": "", "showAxis": True, "showGrid": True, "subtitle": "",
    "titleBold": True, "xAxisBold": False, "yAxisBold": False, "legendBold": False,
    "metricBold": True, "showLegend": True, "themeColor": "professional-blue",
    "titleAlign": "left", "titleColor": "", "xAxisAlign": "", "xAxisColor": "",
    "xAxisTitle": "", "yAxisAlign": "", "yAxisColor": "", "yAxisTitle": "",
    "legendAlign": "", "legendColor": "", "metricAlign": "left", "metricColor": "#4882f6",
    "titleItalic": False, "xAxisItalic": False, "yAxisItalic": False, "legendItalic": False,
    "metricItalic": False, "showSubtitle": True, "subtitleBold": True, "showDataLabel": True,
    "subtitleAlign": "left", "subtitleColor": "#666", "titleFontSize": 16, "xAxisFontSize": 0,
    "yAxisFontSize": 0, "legendFontSize": 0, "legendPosition": "", "metricFontSize": 28,
    "subtitleItalic": False, "titleUnderline": False, "xAxisUnderline": False,
    "yAxisUnderline": False, "legendUnderline": False, "metricUnderline": False,
    "subtitleFontSize": 14, "subtitleUnderline": False, "titleStrikethrough": False,
    "xAxisStrikethrough": False, "yAxisStrikethrough": False, "legendStrikethrough": False,
    "metricStrikethrough": False, "subtitleStrikethrough": False,
}


def _metric_config(field_id: str, display: str, aggregation: str, number_format: dict) -> dict:
    return {
        "sort": None, "fieldId": field_id, "fieldName": display, "isVirtual": True,
        "aggregation": aggregation, "displayName": display, "numberFormat": number_format,
    }


def _data_config(metrics, dimensions=None, extra=None):
    cfg = {
        "filters": [], "metrics": metrics, "dimensions": dimensions or [],
        "fieldOrder": [{"fieldId": m["fieldId"], "fieldType": "metric", "displayName": m["displayName"]}
                       for m in metrics],
        "resultLimit": {"limitType": "all"}, "filtersLogic": "and", "isConfigured": True,
    }
    if extra:
        cfg.update(extra)
    return cfg


NUM = {"type": "number", "decimalPlaces": 0, "thousandSeparator": True}
PCT = {"type": "percent", "decimalPlaces": 1}
DUR = {"type": "duration"}

COMPONENTS = [
    ("今日智能体调用总量", "metric", _data_config(
        [_metric_config("run_count", "调用总量", "sum", NUM)]),
     "ae0f0001c0114d01a000000000000001"),
    ("今日调用成功率", "metric", _data_config(
        [_metric_config("run_success_rate", "调用成功率", "sum", PCT)]),
     "ae0f0001c0114d01a000000000000002"),
    ("今日平均响应时间", "metric", _data_config(
        [_metric_config("avg_process_time", "平均响应时间", "average", DUR)]),
     "ae0f0001c0114d01a000000000000003"),
    ("今日最大响应时间", "metric", _data_config(
        [_metric_config("max_process_time", "最大响应时间", "max", DUR)]),
     "ae0f0001c0114d01a000000000000004"),
    ("智能体调用趋势", "area", _data_config(
        [_metric_config("run_count", "调用总量", "sum", NUM)],
        dimensions=[{"sort": None, "fieldId": "timestamp", "fieldName": "时间(天)",
                     "displayName": "时间(天)", "timeGranularity": "day"}]),
     "ae0f0001c0114d01a000000000000005"),
    ("各智能体调用分布", "stacked-bar", _data_config(
        [_metric_config("run_count", "调用总量", "sum", NUM)],
        dimensions=[{"sort": None, "fieldId": "app_name", "fieldName": "应用名称",
                     "displayName": "应用名称"}]),
     "ae0f0001c0114d01a000000000000006"),
    ("智能体运行状态", "table", _data_config(
        [_metric_config("run_count", "调用总量", "sum", NUM),
         _metric_config("run_success_rate", "调用成功率", "sum", PCT),
         _metric_config("avg_process_time", "平均响应时间", "average", DUR),
         _metric_config("max_process_time", "最大响应时间", "max", DUR)],
        dimensions=[{"sort": None, "fieldId": "app_name", "fieldName": "应用名称",
                     "displayName": "应用名称"}]),
     "ae0f0001c0114d01a000000000000007"),
    ("调用日志", "table", _data_config(
        [], extra={"source": "call_logs", "linkedComponentIds": [QUERY_COMPONENT_ID]}),
     "ae0f0001c0114d01a000000000000008"),
]

_LAYOUTS = [
    {"h": 3, "i": "ae0f0001c0114d01a000000000000001", "w": 6, "x": 0, "y": 21},
    {"h": 3, "i": "ae0f0001c0114d01a000000000000002", "w": 6, "x": 6, "y": 21},
    {"h": 3, "i": "ae0f0001c0114d01a000000000000003", "w": 6, "x": 12, "y": 21},
    {"h": 3, "i": "ae0f0001c0114d01a000000000000004", "w": 6, "x": 18, "y": 21},
    {"h": 8, "i": "ae0f0001c0114d01a000000000000005", "w": 12, "x": 0, "y": 24},
    {"h": 8, "i": "ae0f0001c0114d01a000000000000006", "w": 12, "x": 12, "y": 24},
    {"h": 8, "i": "ae0f0001c0114d01a000000000000007", "w": 12, "x": 0, "y": 32},
    {"h": 8, "i": "ae0f0001c0114d01a000000000000008", "w": 12, "x": 12, "y": 32},
]


def main() -> None:
    with get_sync_db_session() as session:
        # 1. dataset row (schema_config is DB-side; reuse init_dataset.py's declaration
        #    by importing it would duplicate — here we only ensure the row exists and
        #    leave schema_config untouched if present)
        existing_dataset = session.exec(
            select(DashboardDataset).where(DashboardDataset.dataset_code == DATASET_CODE)
        ).first()
        if not existing_dataset:
            from bisheng.telemetry_search.domain.init_dataset import DASHBOARD_DATASET

            seed = next(d for d in DASHBOARD_DATASET if d.dataset_code == DATASET_CODE)
            session.add(DashboardDataset(**seed.model_dump()))
            print(f"dataset {DATASET_CODE} inserted")
        else:
            print(f"dataset {DATASET_CODE} exists")

        # 2. components (upsert by id)
        for title, comp_type, data_config, comp_id in COMPONENTS:
            row = session.get(DashboardComponent, comp_id)
            if not row:
                session.add(DashboardComponent(
                    dashboard_id=DASHBOARD_ID, title=title, type=comp_type,
                    dataset_code=DATASET_CODE if comp_type != "table" or title != "调用日志" else "",
                    data_config=data_config,
                    style_config={**_STYLE, "title": title},
                    id=comp_id,
                ))
                print(f"component {title} inserted")
            else:
                # refresh data_config in place (idempotent upsert)
                row.data_config = data_config
                print(f"component {title} updated")

        # 3. link new components to the query (date filter) component
        query_comp = session.get(DashboardComponent, QUERY_COMPONENT_ID)
        if query_comp:
            # NOTE: data_config is a plain JSON column — in-place mutation of the
            # loaded dict is invisible to SQLAlchemy's dirty check (the dict is
            # compared against itself), so the UPDATE is silently skipped. Build a
            # fresh dict and assign it back.
            cfg = dict(query_comp.data_config or {})
            linked = list(cfg.get("linkedComponentIds") or [])
            new_ids = [c[3] for c in COMPONENTS]
            changed = False
            for cid in new_ids:
                if cid not in linked:
                    linked.append(cid)
                    changed = True
            if changed:
                cfg["linkedComponentIds"] = linked
                query_comp.data_config = cfg
                print("query component linkedComponentIds updated")

        # 4. dashboard layout
        dashboard = session.get(Dashboard, DASHBOARD_ID)
        if dashboard:
            # Same JSON-column caveat as step 3: assign a fresh dict, never mutate
            # dashboard.layout_config in place.
            layout = dict(dashboard.layout_config or {})
            layouts = list(layout.get("layouts") or [])
            existing_ids = {item.get("i") for item in layouts}
            for item in _LAYOUTS:
                if item["i"] not in existing_ids:
                    layouts.append(item)
            layout["layouts"] = layouts
            dashboard.layout_config = layout
            print("dashboard layout updated")

        session.commit()
    print("ops_seed_agent_monitoring done")


if __name__ == "__main__":
    main()
