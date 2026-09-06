"""E2E tests for F052 agent-monitor transaction endpoints.

Runs against the real backend (localhost:7860) with middleware present.
Login: admin via E2E_ADMIN_PASSWORD env (local dev: Tansun@2020).

Covers:
- AC-07/AC-09: transaction list shape, time-desc order, one record per run
- AC-08: status / app_type filters and the 90-day window cap
- AC-15: nodes endpoint for a historical run returns an empty node array
- AC-18: user without the log web menu gets rejected (admin passes)
"""
import time

import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200
from test.e2e.helpers.auth import auth_headers, get_admin_token, get_user_token

TEST_USER = 'test'
TEST_PASSWORD = 'Tansun@2020'

LIST_URL = f'{API_BASE}/telemetry/agent-transactions'


@pytest.mark.e2e
async def test_transaction_list_shape_and_order():
    """AC-07/AC-09: paginated list, time-desc, one record per run, full field set."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_admin_token(client)
        resp = await client.get(LIST_URL, params={'page': 1, 'page_size': 20}, headers=auth_headers(token))
        data = assert_resp_200(resp)

        assert {'records', 'total', 'page', 'page_size'} <= set(data)
        assert data['page'] == 1
        assert data['total'] >= 1
        records = data['records']
        assert len(records) <= 20

        for rec in records:
            assert {'trace_id', 'app_name', 'app_type', 'status', 'process_time', 'input_snapshot', 'time'} <= set(rec)
            assert rec['status'] in ('success', 'failed')

        # AC-09: one transaction per run — trace ids unique within the page
        trace_ids = [rec['trace_id'] for rec in records]
        assert len(set(trace_ids)) == len(trace_ids)

        # time-desc order
        times = [rec['time'] for rec in records]
        assert times == sorted(times, reverse=True)


@pytest.mark.e2e
async def test_transaction_filters_and_window_cap():
    """AC-08: status/app_type filters narrow results; >90d window is rejected."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_admin_token(client)

        resp = await client.get(
            LIST_URL, params={'page': 1, 'page_size': 50, 'status': 'success'}, headers=auth_headers(token)
        )
        data = assert_resp_200(resp)
        assert all(rec['status'] == 'success' for rec in data['records'])

        resp = await client.get(
            LIST_URL, params={'page': 1, 'page_size': 50, 'app_type': 'workflow'}, headers=auth_headers(token)
        )
        data = assert_resp_200(resp)
        assert all(rec['app_type'] == 'workflow' for rec in data['records'])

        # keyword filter is accepted even when nothing matches yet (no input_snapshot
        # events before collection went live) — must not error
        resp = await client.get(
            LIST_URL, params={'page': 1, 'page_size': 10, 'keyword': 'zzz-no-match'}, headers=auth_headers(token)
        )
        assert resp.status_code == 200

        # 90-day window cap -> business error (HTTP 200 + status_code 400 body)
        now = int(time.time())
        resp = await client.get(
            LIST_URL,
            params={'start_time': now - 100 * 86400, 'end_time': now},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()['status_code'] == 400


@pytest.mark.e2e
async def test_nodes_and_model_calls_for_historical_run():
    """AC-15: pre-collection runs return an empty node array (hint in UI), not an error."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_admin_token(client)
        # grab any real transaction trace first
        list_resp = await client.get(LIST_URL, params={'page': 1, 'page_size': 1}, headers=auth_headers(token))
        data = assert_resp_200(list_resp)
        assert data['records'], 'no transactions in the environment — run a workflow first'
        trace_id = data['records'][0]['trace_id']

        nodes_resp = await client.get(
            f'{LIST_URL}/{trace_id}/nodes', headers=auth_headers(token)
        )
        nodes_data = assert_resp_200(nodes_resp)
        assert nodes_data['trace_id'] == trace_id
        assert isinstance(nodes_data['nodes'], list)

        calls_resp = await client.get(
            f'{LIST_URL}/{trace_id}/model-calls', headers=auth_headers(token)
        )
        calls_data = assert_resp_200(calls_resp)
        assert calls_data['trace_id'] == trace_id
        assert isinstance(calls_data['records'], list)
        for rec in calls_data['records']:
            assert {'model_name', 'status', 'duration', 'total_token', 'time'} <= set(rec)


@pytest.mark.e2e
async def test_user_without_log_menu_rejected():
    """AC-18: ordinary user without the log web menu cannot use the endpoints.

    The platform converts business errors into HTTP 200 + status_code in the
    UnifiedResponseModel body (the frontend interceptor handles it).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_user_token(client, TEST_USER, TEST_PASSWORD)
        resp = await client.get(LIST_URL, params={'page': 1, 'page_size': 10}, headers=auth_headers(token))
        assert resp.status_code == 200
        assert resp.json()['status_code'] == 403
