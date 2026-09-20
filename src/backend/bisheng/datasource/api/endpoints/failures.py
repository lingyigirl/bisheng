"""Admin failure list (spec AC-42).

Exists to drive governance, not to show off an error log: an admin reads this
list to find which tables are consistently failing and go write their business
descriptions. That is why the rows carry the TABLES involved and a coarse error
CATEGORY -- those two together say "the model keeps guessing wrong about
`orders`, describe it better", where a raw driver message would say only that
something broke.

What the rows deliberately do NOT carry: the result set (there is none -- the
query failed), and the user's question. INV-19 clause 3 continues the INV-11
boundary, so this table holds statements and summaries, and a question is
conversation text. An admin who wants the conversation can read the chat; this
list is not a second copy of it.

Admin-gated: the statements are the customer's schema in SQL form, which is
more than an ordinary user of a chat product has any reason to read.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.datasource.domain.models.dao import DbQueryFailureDao

router = APIRouter(prefix='/datasource', tags=['datasource'])


@router.get('/query-failures')
async def list_query_failures(
    *,
    datasource_id: int | None = Query(None, description='按数据源筛选'),
    error_category: str | None = Query(None, description='错误类别'),
    start_time: datetime | None = Query(None, description='开始时间(含)'),
    end_time: datetime | None = Query(None, description='结束时间(含)'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Page through failed queries, newest first.

    Tenant-scoped by the DAO's `strict_tenant_filter`, so one tenant's failures
    are never visible to another. The `tables` field is what turns a row into an
    action ("go describe this table"), which is the whole point of the page.
    """
    rows, total = await DbQueryFailureDao.alist(
        datasource_id=datasource_id,
        error_category=error_category,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    return resp_200(
        {
            'data': [
                {
                    'id': row.id,
                    'datasource_id': row.datasource_id,
                    # The failing attempts, so an admin can see what the model
                    # actually tried rather than only that it failed.
                    'statements': row.statements or [],
                    'tables': row.tables or [],
                    'error_category': row.error_category,
                    'create_time': row.create_time,
                }
                for row in rows
            ],
            'total': total,
            'page': page,
            'page_size': page_size,
        }
    )
