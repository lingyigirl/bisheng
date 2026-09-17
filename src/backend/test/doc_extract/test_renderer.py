"""Rendering extracted fields through a template's render form.

Covers spec AC-28 (readable output consistent with the structured result) and
AC-29 (a field that was not extracted renders as empty, without breaking the
rest).

The syntax here is ``{{field_key}}``. It is deliberately NOT
``PromptTemplateParser``'s ``{{#var#}}`` -- that one strips variables back out
(design decision 7 / pitfall 8).
"""

from bisheng.doc_extract.domain.services.renderer import extract_referenced_keys, render


def test_simple_substitution():
    out = render(
        '## {{company_name}}\n统一社会信用代码：{{credit_code}}',
        {'company_name': '广西某某有限公司', 'credit_code': '91450100MA5K'},
    )
    assert out == '## 广西某某有限公司\n统一社会信用代码：91450100MA5K'


def test_missing_optional_field_renders_empty_without_placeholder():
    """AC-29: no exception, no leftover `{{...}}`, other fields unaffected."""
    out = render(
        '名称：{{company_name}}\n期限：{{valid_to}}\n代码：{{credit_code}}',
        {'company_name': 'A', 'credit_code': 'B'},
    )
    assert out == '名称：A\n期限：\n代码：B'
    assert '{{' not in out
    assert '}}' not in out


def test_none_value_renders_empty():
    out = render('期限：{{valid_to}}', {'valid_to': None})
    assert out == '期限：'


def test_empty_render_template_produces_empty_string():
    """AC-11: an empty render form is legal and simply yields nothing."""
    assert render('', {'company_name': 'A'}) == ''
    assert render(None, {'company_name': 'A'}) == ''


def test_non_string_values_are_stringified():
    out = render('{{capital}} / {{is_long_term}}', {'capital': 1000, 'is_long_term': True})
    assert out == '1000 / True'


def test_template_without_placeholders_passes_through():
    assert render('固定文案', {'a': 1}) == '固定文案'


def test_repeated_placeholder_fills_every_occurrence():
    out = render('{{a}}-{{a}}-{{a}}', {'a': 'x'})
    assert out == 'x-x-x'


def test_whitespace_inside_braces_is_tolerated():
    out = render('{{ company_name }}', {'company_name': 'A'})
    assert out == 'A'


def test_adjacent_placeholders():
    out = render('{{a}}{{b}}', {'a': '1', 'b': '2'})
    assert out == '12'


# --- extract_referenced_keys: used at save time to reject dangling references ---


def test_extract_referenced_keys_finds_all():
    keys = extract_referenced_keys('{{a}} text {{b}} text {{a}}')
    assert keys == {'a', 'b'}


def test_extract_referenced_keys_empty_when_no_placeholders():
    assert extract_referenced_keys('no placeholders here') == set()


def test_extract_referenced_keys_ignores_empty_braces():
    assert extract_referenced_keys('{{}} {{ }}') == set()
