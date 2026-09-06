"""F054: form payload building — label mapping, file signing, all-item coverage."""

from bisheng.evaluation.domain.services.evaluation_service import (
    _resolve_file_value,
    build_form_payload,
)


class _FakeMinio:
    tmp_bucket = "tmp-dir"
    bucket = "bisheng"

    def __init__(self):
        self.calls = []

    def get_share_link_sync(self, object_name, bucket=None, clear_host=False, expire_days=1):
        self.calls.append((bucket, object_name))
        return f"http://minio/{bucket}/{object_name}?X-Amz=demo"


def _schema():
    return {
        "tab": "form_input",
        "value": [
            {"key": "file_abc", "type": "file", "value": "银行流水文件", "required": True},
            {"key": "loan_principal", "type": "text", "value": "贷款本金（万元，可选）", "required": False},
            {"key": "main_biz", "type": "text", "value": "主营业务", "required": False},
        ],
    }


def test_builds_payload_for_all_schema_items():
    """AC-05: every schema item is covered — optional text defaults to empty."""
    values = {
        "银行流水文件": ["tmp-dir/bank.csv"],
        "贷款本金（万元，可选）": "500",
    }
    payload, err = build_form_payload(_schema(), values, _FakeMinio())
    assert err is None
    assert payload == {
        "file_abc": ["http://minio/tmp-dir/bank.csv?X-Amz=demo"],
        "loan_principal": "500",
        "main_biz": "",
    }


def test_unknown_label_fails():
    payload, err = build_form_payload(_schema(), {"不存在的表单项": "x"}, _FakeMinio())
    assert payload is None
    assert "不存在的表单项" in err


def test_duplicate_label_in_schema_fails():
    schema = {
        "tab": "form_input",
        "value": [
            {"key": "a", "type": "text", "value": "重复标签"},
            {"key": "b", "type": "text", "value": "重复标签"},
        ],
    }
    payload, err = build_form_payload(schema, {}, _FakeMinio())
    assert payload is None
    assert "重复" in err


def test_missing_required_file_fails():
    payload, err = build_form_payload(_schema(), {}, _FakeMinio())
    assert payload is None
    assert "银行流水文件" in err


def test_string_file_value_wrapped_in_list():
    """AC-02: a plain string is treated as a single-file array."""
    values = {"银行流水文件": "tmp-dir/bank.csv"}
    payload, err = build_form_payload(_schema(), values, _FakeMinio())
    assert err is None
    assert payload["file_abc"] == ["http://minio/tmp-dir/bank.csv?X-Amz=demo"]


def test_full_url_passes_through():
    """AC-03: values with a scheme are used as-is (no signing)."""
    values = {"银行流水文件": ["http://example.com/x.csv"]}
    payload, err = build_form_payload(_schema(), values, _FakeMinio())
    assert err is None
    assert payload["file_abc"] == ["http://example.com/x.csv"]


def test_path_bucket_rules():
    """Design 决策 2: tmp-dir/ prefix -> tmp bucket, others -> default bucket."""
    m = _FakeMinio()
    assert _resolve_file_value("tmp-dir/a.csv", m).startswith("http://minio/tmp-dir/a.csv")
    assert _resolve_file_value("evaluation/dataset/x.csv", m).startswith("http://minio/bisheng/evaluation/dataset/x.csv")
    assert _resolve_file_value("http://already/url", m) == "http://already/url"
