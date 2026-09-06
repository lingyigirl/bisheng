"""Unit tests for the MCP tool Chinese display dictionary."""
from types import SimpleNamespace

from bisheng.tool.domain.zh_dict import apply_zh_dict, find_server_dict

ZJS_URL = 'https://mcp.cnfic.com.cn/bdc-mcp-service/mcp/zjs_company/http/'


def _fake_tool(name: str, desc: str = 'English desc', params: dict | None = None) -> SimpleNamespace:
    schema = {'type': 'object', 'properties': {}}
    for pname, pdesc in (params or {}).items():
        schema['properties'][pname] = {'type': 'STRING', 'description': pdesc}
    return SimpleNamespace(name=name, description=desc, inputSchema=schema)


def test_find_server_dict_matches_prefix():
    entry = find_server_dict(ZJS_URL)
    assert entry is not None
    assert 'get_company_profile' in entry['tools']


def test_find_server_dict_unmatched():
    assert find_server_dict('https://other.example.com/mcp/foo') is None


def test_apply_zh_dict_overrides_desc_and_params():
    tool = _fake_tool(
        'get_company_profile',
        desc='Lookup a company basic profile.',
        params={'query': 'Search value', 'query_type': 'Query type', 'time': 'Time filter'},
    )
    apply_zh_dict(ZJS_URL, [tool])

    assert '查询企业基础画像' in tool.description
    assert '查询值' in tool.inputSchema['properties']['query']['description']
    assert '查询类型' in tool.inputSchema['properties']['query_type']['description']
    # params not present in the dictionary stay untouched
    assert tool.inputSchema['properties']['time']['description'] == 'Time filter'


def test_apply_zh_dict_ignores_unmatched_server():
    tool = _fake_tool('get_company_profile')
    apply_zh_dict('https://other.example.com/mcp/foo', [tool])
    assert tool.description == 'English desc'


def test_apply_zh_dict_ignores_unknown_tool():
    tool = _fake_tool('some_unknown_tool')
    apply_zh_dict(ZJS_URL, [tool])
    assert tool.description == 'English desc'
