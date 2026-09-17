"""Field-level validation of model output against a template's field schema.

Covers spec AC-14 (required vs optional), AC-15 (empty string == missing) and
AC-26 / AC-27 (what counts as "does not match the definition").
"""

from bisheng.doc_extract.domain.services.field_validator import validate_fields

SCHEMA = [
    {'key': 'company_name', 'label': '企业名称', 'type': 'string', 'required': True},
    {'key': 'credit_code', 'label': '统一社会信用代码', 'type': 'string', 'required': True},
    {'key': 'capital', 'label': '注册资本', 'type': 'number', 'required': False},
    {'key': 'valid_to', 'label': '营业期限至', 'type': 'date', 'required': False},
    {'key': 'is_long_term', 'label': '长期有效', 'type': 'boolean', 'required': False},
]


def test_all_required_fields_present_passes():
    ok, errors = validate_fields(
        {'company_name': '广西某某有限公司', 'credit_code': '91450100MA5K'},
        SCHEMA,
    )
    assert ok is True
    assert errors == []


def test_missing_required_field_fails():
    ok, errors = validate_fields({'company_name': '广西某某有限公司'}, SCHEMA)
    assert ok is False
    assert [e['field_key'] for e in errors] == ['credit_code']


def test_missing_optional_field_does_not_fail():
    """AC-14: an optional field simply not extracted is not a failure."""
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B'},
        SCHEMA,
    )
    assert ok is True
    assert errors == []


def test_empty_string_is_treated_as_missing_for_required():
    """AC-15: empty string and absent are the same thing."""
    ok, errors = validate_fields(
        {'company_name': '', 'credit_code': 'B'},
        SCHEMA,
    )
    assert ok is False
    assert [e['field_key'] for e in errors] == ['company_name']


def test_empty_string_is_treated_as_missing_for_optional():
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'capital': ''},
        SCHEMA,
    )
    assert ok is True
    assert errors == []


def test_type_mismatch_fails():
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'capital': 'not-a-number'},
        SCHEMA,
    )
    assert ok is False
    assert [e['field_key'] for e in errors] == ['capital']


def test_number_accepts_numeric_string():
    """Models routinely emit numbers as strings; that is still a number."""
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'capital': '1000'},
        SCHEMA,
    )
    assert ok is True


def test_number_accepts_int_and_float():
    for value in (1000, 1000.5):
        ok, _ = validate_fields(
            {'company_name': 'A', 'credit_code': 'B', 'capital': value},
            SCHEMA,
        )
        assert ok is True, value


def test_number_rejects_bool():
    """bool is an int subclass in Python -- it must not slip through as a number."""
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'capital': True},
        SCHEMA,
    )
    assert ok is False
    assert [e['field_key'] for e in errors] == ['capital']


def test_boolean_accepts_bool_and_common_string_forms():
    for value in (True, False, 'true', 'false', '是', '否'):
        ok, _ = validate_fields(
            {'company_name': 'A', 'credit_code': 'B', 'is_long_term': value},
            SCHEMA,
        )
        assert ok is True, value


def test_boolean_rejects_arbitrary_string():
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'is_long_term': 'maybe'},
        SCHEMA,
    )
    assert ok is False
    assert [e['field_key'] for e in errors] == ['is_long_term']


def test_date_accepts_common_formats():
    for value in ('2026-09-17', '2026/09/17', '2026年9月17日'):
        ok, _ = validate_fields(
            {'company_name': 'A', 'credit_code': 'B', 'valid_to': value},
            SCHEMA,
        )
        assert ok is True, value


def test_date_rejects_free_text():
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'valid_to': '长期'},
        SCHEMA,
    )
    assert ok is False
    assert [e['field_key'] for e in errors] == ['valid_to']


def test_extra_fields_are_ignored_and_pass():
    """AC-27: fields outside the definition are dropped, not a failure."""
    ok, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'unexpected': 'x'},
        SCHEMA,
    )
    assert ok is True
    assert errors == []


def test_error_carries_key_expected_and_actual():
    """AC-26: retry feedback needs to name the field and what went wrong."""
    _, errors = validate_fields(
        {'company_name': 'A', 'credit_code': 'B', 'capital': 'abc'},
        SCHEMA,
    )
    err = errors[0]
    assert err['field_key'] == 'capital'
    assert err['expected'] == 'number'
    assert err['actual'] == 'abc'


def test_multiple_errors_are_all_reported():
    ok, errors = validate_fields({'capital': 'abc', 'valid_to': '长期'}, SCHEMA)
    assert ok is False
    assert {e['field_key'] for e in errors} == {'company_name', 'credit_code', 'capital', 'valid_to'}


def test_none_value_treated_as_missing():
    ok, errors = validate_fields(
        {'company_name': None, 'credit_code': 'B'},
        SCHEMA,
    )
    assert ok is False
    assert [e['field_key'] for e in errors] == ['company_name']


def test_empty_schema_always_passes():
    ok, errors = validate_fields({'anything': 1}, [])
    assert ok is True
    assert errors == []
