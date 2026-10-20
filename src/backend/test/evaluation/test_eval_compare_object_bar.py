"""F090: 对比对象条 — the two versions' full model / prompt-node lists + which
changed (换模型 / 换提示词 / both)."""

from bisheng.evaluation.domain.services.evaluation_compare_report import build_object_bar


def _node(node_id="n1", name="LLM1", model_id=5, system_prompt="S", user_prompt="U"):
    return {
        "id": node_id,
        "data": {
            "type": "llm",
            "name": name,
            "group_params": [
                {
                    "params": [
                        {"key": "model_id", "value": model_id},
                        {"key": "system_prompt", "value": system_prompt},
                        {"key": "user_prompt", "value": user_prompt},
                    ]
                }
            ],
        },
    }


def _flow(*nodes):
    return {"nodes": list(nodes), "edges": []}


_MODELS = {5: "qwen-turbo", 7: "qwen-plus"}


class TestObjectBar:
    def test_identical_versions_have_no_change(self):
        bar = build_object_bar(_flow(_node()), _flow(_node()), model_names=_MODELS)
        assert bar["change_kind"] == "none"
        assert bar["models"]["v1"] == ["qwen-turbo"]
        assert bar["models"]["v2"] == ["qwen-turbo"]
        assert bar["prompt_nodes"]["v1"] == ["LLM1"]

    def test_model_only_change(self):
        bar = build_object_bar(_flow(_node(model_id=5)), _flow(_node(model_id=7)), model_names=_MODELS)
        assert bar["change_kind"] == "model"
        assert bar["models"]["v2"] == ["qwen-plus"]

    def test_prompt_only_change(self):
        bar = build_object_bar(
            _flow(_node(system_prompt="A")),
            _flow(_node(system_prompt="B")),
            model_names=_MODELS,
        )
        assert bar["change_kind"] == "prompt"

    def test_both_changed_is_multi_variable(self):
        bar = build_object_bar(
            _flow(_node(model_id=5, user_prompt="X")),
            _flow(_node(model_id=7, user_prompt="Y")),
            model_names=_MODELS,
        )
        assert bar["change_kind"] == "both"

    def test_resolves_all_models_and_lists_all_prompt_nodes(self):
        # two LLM nodes per version, one of them unchanged
        v1 = _flow(_node("n1", "LLM1", 5), _node("n2", "LLM2", 7))
        v2 = _flow(_node("n1", "LLM1", 5), _node("n2", "LLM2", 7))
        bar = build_object_bar(v1, v2, model_names=_MODELS)
        assert bar["models"]["v1"] == ["qwen-turbo", "qwen-plus"]
        assert bar["prompt_nodes"]["v1"] == ["LLM1", "LLM2"]

    def test_unknown_model_id_falls_back_to_id_string(self):
        bar = build_object_bar(_flow(_node(model_id=99)), _flow(_node(model_id=99)), model_names=_MODELS)
        assert bar["models"]["v1"] == ["99"]
