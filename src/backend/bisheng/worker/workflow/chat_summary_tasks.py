"""工作流对话摘要 Celery 任务。"""

from bisheng.chat_session.domain.services.chat_turn_summary_service import ChatTurnSummaryService
from bisheng.worker.main import bisheng_celery


@bisheng_celery.task
def summarize_chat_turn(turn_id: str) -> None:
    ChatTurnSummaryService.summarize_turn(turn_id)
