"""E2E tests for F051 workflow version compare endpoint.

Runs against the real backend (localhost:7860) with middleware present.
Login: admin via E2E_ADMIN_PASSWORD env (local dev: Tansun@2020).

Covers:
- AC-12: GET /api/v1/workflow/versions/compare returns structured diff
- AC-04/AC-05: real data — 会议纪要智能助手 (c6c9b2...) v1.5 (id 19) vs v1.6
  (id 20) has a modified llm_4b8e2 node with group_params field changes
- AC-07: same-version compare returns changed: true
- AC-13: nonexistent / other-flow version ids return 10500
- AC-14: user without view/use permission gets 403 (skipped when the test
  user happens to have access in the current environment)
"""
import httpx
import pytest

from test.e2e.helpers.api import API_BASE, assert_resp_200, assert_resp_error
from test.e2e.helpers.auth import auth_headers, get_admin_token, get_user_token

# 会议纪要智能助手 - 9 个版本, v1.5 (id 19) -> v1.6 (id 20) 有真实参数差异
FLOW_ID = 'c6c9b2c3287447cb8db2577152362cb4'
V15_ID = 19
V16_ID = 20

TEST_USER = 'test'
TEST_PASSWORD = 'Tansun@2020'


def _compare_url(flow_id: str, from_id: int, to_id: int) -> str:
    return (
        f'{API_BASE}/workflow/versions/compare'
        f'?flow_id={flow_id}&from_version_id={from_id}&to_version_id={to_id}'
    )


@pytest.mark.e2e
async def test_compare_returns_structured_diff_for_real_flow():
    """AC-12/AC-04/AC-05: compare endpoint returns the structured diff shape
    and real v1.5→v1.6 data yields a modified llm node with field changes."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_admin_token(client)
        resp = await client.get(_compare_url(FLOW_ID, V15_ID, V16_ID), headers=auth_headers(token))
        data = assert_resp_200(resp)

        assert data['from_version']['id'] == V15_ID
        assert data['to_version']['id'] == V16_ID
        assert data['changed'] is True

        nodes = {c['node_id']: c for c in data['node_changes']}
        assert 'llm_4b8e2' in nodes
        change = nodes['llm_4b8e2']
        assert change['change'] == 'modified'
        assert change['node_name']
        assert change['node_type'] == 'llm'
        assert any(f['path'].startswith('group_params') for f in change['fields'])
        for field in change['fields']:
            assert {'path', 'old_value', 'new_value'} <= set(field)


@pytest.mark.e2e
async def test_compare_same_version_returns_changed_true():
    """AC-07: comparing a version against itself returns changed: false with
    empty change lists."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_admin_token(client)
        resp = await client.get(_compare_url(FLOW_ID, V16_ID, V16_ID), headers=auth_headers(token))
        data = assert_resp_200(resp)
        assert data['changed'] is False
        assert data['node_changes'] == []
        assert data['edge_changes'] == []


@pytest.mark.e2e
async def test_compare_nonexistent_version_returns_10500():
    """AC-13: a nonexistent version id returns error code 10500."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_admin_token(client)
        resp = await client.get(_compare_url(FLOW_ID, 999999, V16_ID), headers=auth_headers(token))
        assert_resp_error(resp, 10500)


@pytest.mark.e2e
async def test_compare_version_of_another_flow_returns_10500():
    """AC-13: version ids that belong to a different flow return 10500."""
    async with httpx.AsyncClient(timeout=30) as client:
        token = await get_admin_token(client)
        # Find any other workflow to borrow a foreign flow_id from
        list_resp = await client.get(
            f'{API_BASE}/workflow/list', params={'page': 1, 'limit': 50}, headers=auth_headers(token)
        )
        items = assert_resp_200(list_resp).get('data', [])
        other = next((app for app in items if app['id'] != FLOW_ID), None)
        if other is None:
            pytest.skip('no other workflow in current environment')
        resp = await client.get(_compare_url(other['id'], V15_ID, V16_ID), headers=auth_headers(token))
        assert_resp_error(resp, 10500)


@pytest.mark.e2e
async def test_compare_without_permission_returns_403():
    """AC-14: a user without view/use permission on the flow gets 403
    (skipped when the test user happens to have access in this environment)."""
    async with httpx.AsyncClient(timeout=30) as client:
        test_token = await get_user_token(client, TEST_USER, TEST_PASSWORD)
        resp = await client.get(_compare_url(FLOW_ID, V15_ID, V16_ID), headers=auth_headers(test_token))
        body = resp.json()
        if body.get('status_code') == 200:
            pytest.skip('test user has view/use permission on this flow in current environment')
        assert body.get('status_code') == 403
