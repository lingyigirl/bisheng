from __future__ import annotations

from typing import Any


class AgentPublishApprovalHandler:
    """智能体发布审批 Handler（agent_publish_request 场景）。

    发布业务本身不在本类执行——审批通过后由 outbox 执行体 ``publish_app``
    （api 层工厂注入，见 ``api/services/agent_publish.py``）真正上线应用。
    本类只负责标题/详情/审批人解析与生命周期回调；执行失败按「业务回调不得
    静默失败」原则 raise，由 outbox 置 FAILED 并产生 execute_failed 异常。
    """

    scenario_code = 'agent_publish_request'

    def __init__(self, *, publish_app: Any = None) -> None:
        self.publish_app = publish_app

    async def validate(self, req, login_user) -> None:
        return None

    async def build_title(self, req) -> str:
        return f'「{req.business_name}」发布申请'

    async def build_detail(self, req) -> dict:
        return {
            'app_name': req.business_name,
            'app_type': req.payload_snapshot.get('app_type'),
        }

    async def build_business_link(self, req) -> dict:
        return {}

    async def resolve_approvers(self, node_config: dict, req) -> list[int]:
        from bisheng.approval.domain.services.approver_resolver import resolve_approvers_from_sources

        sources = node_config.get('sources') or []
        if not sources:
            approver_ids = node_config.get('approver_user_ids') or node_config.get('user_ids') or []
            return [int(one) for one in approver_ids]
        return await resolve_approvers_from_sources(sources, req)

    async def on_approved(self, instance_id: int, payload_snapshot: dict) -> dict:
        if self.publish_app is None:
            raise RuntimeError('agent publish handler has no publish_app executor')
        if not isinstance(payload_snapshot, dict) or not payload_snapshot:
            raise ValueError(f'publish payload_snapshot missing for instance={instance_id}')
        await self.publish_app(payload_snapshot)
        return {'status': 'published'}

    async def on_rejected(self, instance_id: int, payload_snapshot: dict, reason: str | None) -> None:
        return None

    async def on_withdrawn(self, instance_id: int, payload_snapshot: dict, reason: str | None) -> None:
        return None

    async def on_cancelled(self, instance_id: int, payload_snapshot: dict, reason: str | None) -> None:
        return None
