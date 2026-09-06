"""Tests for the flow version diff pure function (F051-flow-version-compare).

Covers the normalization rules from design.md 决策 2: node matching by id on
``node.data`` only (position/viewport excluded), param lists aligned by their
``key`` field, edges matched by connection tuple (generated edge.id ignored),
and the missing-vs-empty value distinction.
"""

from bisheng.common.utils.flow_version_diff import compare_flow_versions


def _node(node_id: str, name: str, node_type: str = 'llm', **data_extra) -> dict:
    data = {'name': name, 'type': node_type, 'description': '', **data_extra}
    return {'id': node_id, 'data': data, 'type': node_type, 'position': {'x': 0, 'y': 0}}


def _edge(source: str, target: str, source_handle: str = 'right_handle', target_handle: str = 'left_handle') -> dict:
    return {
        'id': f'xy-edge__{source}{source_handle}-{target}{target_handle}',
        'type': 'customEdge',
        'source': source,
        'target': target,
        'animated': True,
        'sourceHandle': source_handle,
        'targetHandle': target_handle,
    }


def _flow(nodes: list[dict], edges: list[dict], viewport: dict | None = None) -> dict:
    return {'nodes': nodes, 'edges': edges, 'viewport': viewport or {'x': 0, 'y': 0, 'zoom': 0.6}}


def _group_params(value: str) -> list:
    """Shape of a real node.data.group_params entry (design §4.2)."""
    return [{'params': [{'key': 'model', 'value': value, 'tab': 'base', 'help': '模型'}]}]


class TestNodeLevelChanges:
    def test_node_added_removed_modified(self):
        data_from = _flow(
            [_node('a', '节点A', group_params=_group_params('v1')), _node('b', '节点B')],
            [_edge('a', 'b')],
        )
        data_to = _flow(
            [_node('a', '节点A', group_params=_group_params('v2')), _node('c', '节点C')],
            [_edge('a', 'c')],
        )
        result = compare_flow_versions(data_from, data_to)

        assert result['changed'] is True
        by_id = {c['node_id']: c for c in result['node_changes']}
        assert set(by_id) == {'a', 'b', 'c'}
        assert by_id['b']['change'] == 'removed'
        assert by_id['b']['node_name'] == '节点B'
        assert by_id['c']['change'] == 'added'
        assert by_id['a']['change'] == 'modified'
        assert by_id['a']['node_type'] == 'llm'

    def test_modified_node_fields_carry_path_and_old_new_values(self):
        data_from = _flow([_node('a', '节点A', group_params=_group_params('v1'))], [])
        data_to = _flow([_node('a', '节点A', group_params=_group_params('v2'))], [])

        result = compare_flow_versions(data_from, data_to)
        fields = result['node_changes'][0]['fields']
        assert len(fields) == 1
        field = fields[0]
        assert field['path'] == 'group_params[0].params[model].value'
        assert field['old_value'] == 'v1'
        assert field['new_value'] == 'v2'


class TestNormalizationRules:
    def test_param_order_shuffle_produces_no_diff(self):
        params_v1 = [{'key': 'a', 'value': 1}, {'key': 'b', 'value': 2}]
        params_v2 = [{'key': 'b', 'value': 2}, {'key': 'a', 'value': 1}]
        data_from = _flow([_node('a', '节点A', group_params=[{'params': params_v1}])], [])
        data_to = _flow([_node('a', '节点A', group_params=[{'params': params_v2}])], [])

        result = compare_flow_versions(data_from, data_to)
        assert result['changed'] is False
        assert result['node_changes'] == []

    def test_position_and_viewport_changes_are_ignored(self):
        node_from = _node('a', '节点A')
        node_to = _node('a', '节点A')
        node_to['position'] = {'x': 300, 'y': 500}
        data_from = _flow([node_from], [], viewport={'x': 0, 'y': 0, 'zoom': 0.6})
        data_to = _flow([node_to], [], viewport={'x': -120, 'y': 40, 'zoom': 1.4})

        result = compare_flow_versions(data_from, data_to)
        assert result['changed'] is False

    def test_edge_matched_by_connection_tuple_ignoring_generated_id(self):
        e1 = _edge('a', 'b')
        e2 = _edge('a', 'b')
        e2['id'] = 'xy-edge__completely-different-generated-id'
        result = compare_flow_versions(_flow([_node('a', 'A'), _node('b', 'B')], [e1]),
                                       _flow([_node('a', 'A'), _node('b', 'B')], [e2]))
        assert result['changed'] is False
        assert result['edge_changes'] == []

    def test_edge_added_and_removed(self):
        data_from = _flow([_node('a', 'A'), _node('b', 'B')], [_edge('a', 'b')])
        data_to = _flow([_node('a', 'A'), _node('b', 'B')], [_edge('b', 'a')])
        result = compare_flow_versions(data_from, data_to)
        assert result['changed'] is True
        changes = {(c['source'], c['target'], c['change']) for c in result['edge_changes']}
        assert changes == {('a', 'b', 'removed'), ('b', 'a', 'added')}


class TestMissingVsEmpty:
    def test_key_missing_on_one_side_is_add_or_remove_field(self):
        data_from = _flow([_node('a', 'A', group_params=_group_params('v1'))], [])
        data_to = _flow([_node('a', 'A', group_params=[{'params': [{'key': 'other', 'value': 'x'}]}])], [])

        result = compare_flow_versions(data_from, data_to)
        fields = result['node_changes'][0]['fields']
        by_path = {f['path']: f for f in fields}
        assert by_path['group_params[0].params[model]']['old_value'] == 'v1'
        assert by_path['group_params[0].params[model]']['new_value'] is None
        assert by_path['group_params[0].params[other]']['old_value'] is None
        assert by_path['group_params[0].params[other]']['new_value'] == 'x'

    def test_none_vs_empty_string_is_a_modification(self):
        data_from = _flow([_node('a', 'A', description=None)], [])
        data_to = _flow([_node('a', 'A', description='')], [])

        result = compare_flow_versions(data_from, data_to)
        assert result['changed'] is True
        fields = result['node_changes'][0]['fields']
        assert fields[0]['path'] == 'description'
        assert fields[0]['old_value'] is None
        assert fields[0]['new_value'] == ''


class TestIdentical:
    def test_identical_flows(self):
        data = _flow([_node('a', 'A', group_params=_group_params('v1')), _node('b', 'B')], [_edge('a', 'b')])
        result = compare_flow_versions(data, data)
        assert result['changed'] is False
        assert result['node_changes'] == []
        assert result['edge_changes'] == []
