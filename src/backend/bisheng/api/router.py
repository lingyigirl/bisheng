# Router for base api
from fastapi import APIRouter, Depends

from bisheng.admin.api.router import router as admin_router
from bisheng.api.v1 import (
    assistant_router,
    audit_router,
    chat_router,
    endpoints_router,
    flows_router,
    group_router,
    invite_code_router,
    mark_router,
    report_router,
    skillcenter_router,
    tag_router,
    tool_router,
    user_router,
    variable_router,
    workflow_router,
    workstation_router,
)
from bisheng.approval.api.router import router as approval_router
from bisheng.brand.api.router import router as brand_router
from bisheng.channel.api.router import router as channel_router
from bisheng.chat_session.api.router import router as session_router
from bisheng.citation.api.router import router as citation_router
from bisheng.datasource.api.router import router as datasource_router
from bisheng.department.api.router import router as department_router
from bisheng.doc_extract.api.router import router as doc_extract_router
from bisheng.evaluation.api.router import router as evaluation_router
from bisheng.finetune.api.finetune import router as finetune_router
from bisheng.finetune.api.server import router as server_router
from bisheng.gpu_manage.api.router import router as gpu_manage_router
from bisheng.knowledge.api.router import (
    answer_suggestion_router,
    knowledge_graph_router,
    knowledge_router,
    knowledge_space_router,
    knowledge_space_tag_library_router,
    knowledge_version_router,
    qa_router,
)
from bisheng.linsight.api.router import router as linsight_router
from bisheng.llm.api.router import router as llm_router
from bisheng.managed_agent.api.endpoints.taxonomy import router as managed_agent_taxonomy_router
from bisheng.managed_agent.api.router import management_router as managed_agent_management_router
from bisheng.managed_agent.api.router import rpc_router as managed_agent_rpc_router
from bisheng.message.api.router import router as message_router
from bisheng.open_api.api.dependencies import verify_open_api_access
from bisheng.open_api.api.router import management_router as open_api_management_router
from bisheng.open_api.api.router import rpc_router as open_api_rpc_router
from bisheng.open_endpoints.api.endpoints.llm import router as llm_router_rpc
from bisheng.open_endpoints.api.router import (
    assistant_router_rpc,
    chat_router_rpc,
    citation_router_rpc,
    filelib_router_rpc,
    flow_router_rpc,
    knowledge_router_rpc,
    workflow_router_rpc,
)
from bisheng.org_sync.api.endpoints.relink import router as relink_router
from bisheng.org_sync.api.router import router as org_sync_router
from bisheng.permission.api.router import router as permission_router
from bisheng.role.api.router import router as role_router
from bisheng.scheduled_task.api.router import management_router as scheduled_task_management_router
from bisheng.scheduled_task.api.router import rpc_router as scheduled_task_rpc_router
from bisheng.sensitive_word.api.router import router as sensitive_word_policy_router
from bisheng.share_link.api.router import router as share_link_router
from bisheng.sso_sync.api.router import router as sso_sync_router
from bisheng.telemetry_search.api.router import router as telemetry_search_router
from bisheng.tenant.api.router import router as tenant_router
from bisheng.term_library.api.router import router as term_library_router
from bisheng.user_group.api.router import router as user_group_router
from bisheng.workstation.api.endpoints.conversation_export import router as conversation_export_router

router = APIRouter(
    prefix="/api/v1",
)
router.include_router(chat_router)
router.include_router(endpoints_router)
router.include_router(knowledge_router)
router.include_router(knowledge_graph_router)
router.include_router(knowledge_space_tag_library_router)
router.include_router(knowledge_space_router)
router.include_router(knowledge_version_router)
# F059: answer-bar suggestions (keywords + related questions).
router.include_router(answer_suggestion_router)
router.include_router(server_router)
router.include_router(gpu_manage_router)
router.include_router(user_router)
router.include_router(qa_router)
router.include_router(variable_router)
router.include_router(report_router)
router.include_router(finetune_router)
router.include_router(assistant_router)
router.include_router(group_router)
router.include_router(audit_router)
router.include_router(evaluation_router)
router.include_router(tag_router)
router.include_router(llm_router)
router.include_router(workflow_router)
router.include_router(mark_router)
router.include_router(workstation_router)
router.include_router(conversation_export_router)
router.include_router(skillcenter_router)
router.include_router(flows_router)
router.include_router(linsight_router)
router.include_router(tool_router)
router.include_router(invite_code_router)
router.include_router(session_router)
router.include_router(share_link_router)
router.include_router(telemetry_search_router)
router.include_router(channel_router)
router.include_router(message_router)
router.include_router(department_router)
router.include_router(user_group_router)
router.include_router(permission_router)
router.include_router(role_router)
router.include_router(org_sync_router)
router.include_router(sso_sync_router)
router.include_router(relink_router)
router.include_router(tenant_router)
router.include_router(citation_router)
router.include_router(admin_router)
router.include_router(approval_router)
router.include_router(brand_router)
router.include_router(sensitive_word_policy_router)
router.include_router(doc_extract_router)
router.include_router(datasource_router)
router.include_router(term_library_router)

# F076: every /api/v2 call runs through the open-API auth dependency. It is a
# no-op while `open_api.enforce_keys` is off (spec AC-21), so mounting it here
# does not change today's behaviour; endpoints must carry an `@open_api_scope`
# marker or the dependency fails closed with 500/11811 (design §2.5).
router_rpc = APIRouter(prefix="/api/v2", dependencies=[Depends(verify_open_api_access)])
router_rpc.include_router(knowledge_router_rpc)
router_rpc.include_router(filelib_router_rpc)
router_rpc.include_router(chat_router_rpc)
router_rpc.include_router(assistant_router_rpc)
router_rpc.include_router(workflow_router_rpc)
router_rpc.include_router(llm_router_rpc)
router_rpc.include_router(flow_router_rpc)
router_rpc.include_router(citation_router_rpc)
router_rpc.include_router(open_api_rpc_router)
# F077: 定时任务的事件触发端点（POST /api/v2/schedule/trigger）继承上面的密钥鉴权依赖；
# 端点的 scope 在 open_api/domain/scopes.py 逐条登记，漏标即 500/11811（design 决策 12）。
router_rpc.include_router(scheduled_task_rpc_router)
# F078: 纳管智能体的统一调用端点（POST /api/v2/managed-agent/invoke）。
# 与 F077 一样继承上面的密钥鉴权依赖；scope 在 open_api/domain/scopes.py 登记。
router_rpc.include_router(managed_agent_rpc_router)
# F076: 开放 API 管理面(服务账号/密钥/设置)挂在 /api/v1
router.include_router(open_api_management_router)
# F077: 定时任务管理面(CRUD/立即运行/可用运行身份/cron 预览/NL 草稿)挂在 /api/v1，
# 应用级 edit_app 校验在各端点内独立声明（design 决策 11）。
router.include_router(scheduled_task_management_router)
# F078: 纳管智能体管理面（注册/分类/健康/目录）挂在 /api/v1。
router.include_router(managed_agent_management_router)
router.include_router(managed_agent_taxonomy_router)
