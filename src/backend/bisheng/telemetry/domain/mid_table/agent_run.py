"""F046: mid_agent_run_dtl — one row per agent (workflow/assistant/linsight) run."""

from bisheng.common.constants.enums.telemetry import BaseTelemetryTypeEnum
from bisheng.telemetry.domain.mid_table.base import BaseMidTable, BaseRecord


class AgentRunRecord(BaseRecord):
    event_id: str
    app_id: str
    app_name: str
    app_type: str
    status: str | None = None
    process_time: int
    chat_id: str | None = None


class AgentRun(BaseMidTable):
    _index_name = "mid_agent_run_dtl"
    _mappings = {
        "event_id": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "single_char_analyzer"}}},
        "app_id": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "single_char_analyzer"}}},
        "app_name": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "single_char_analyzer"}}},
        "app_type": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "single_char_analyzer"}}},
        "status": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "single_char_analyzer"}}},
        "process_time": {"type": "long"},
        "chat_id": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "single_char_analyzer"}}},
    }

    def get_records_by_time_range_sync(self, start_time: int, end_time: int, page: int = 1, page_size: int = 1000):
        query = {
            "bool": {
                "filter": [
                    {"term": {"event_type": {"value": BaseTelemetryTypeEnum.APPLICATION_PROCESS.value}}},
                    {"range": {"timestamp": {"gte": start_time * 1000, "lt": end_time * 1000}}},
                ]
            }
        }

        return self.search_from_base_sync(body={"query": query, "from": (page - 1) * page_size, "size": page_size})
