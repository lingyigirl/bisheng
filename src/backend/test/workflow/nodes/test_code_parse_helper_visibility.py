"""CodeParser must make sibling helper functions visible inside main().

exec() with separate globals/locals binds defs into locals only; without
mirroring them into globals, calling a helper defined in the same node code
from main() raises NameError (observed on the 会议纪要 workflow's run_ffmpeg).
"""

from bisheng.workflow.nodes.code.code_parse import CodeParser


def test_main_can_call_sibling_helper():
    code = (
        "def helper():\n"
        "    return 42\n"
        "\n"
        "def main():\n"
        "    return helper()\n"
    )
    parser = CodeParser(code)
    parser.parse_code()
    assert parser.exec_method('main') == 42


def test_nested_helpers_resolve_each_other():
    code = (
        "def inner(x):\n"
        "    return x * 2\n"
        "\n"
        "def outer():\n"
        "    return inner(21)\n"
        "\n"
        "def main():\n"
        "    return outer()\n"
    )
    parser = CodeParser(code)
    parser.parse_code()
    assert parser.exec_method('main') == 42


def test_helpers_stay_visible_alongside_imports():
    code = (
        "import math\n"
        "\n"
        "def helper():\n"
        "    return math.floor(2.9)\n"
        "\n"
        "def main():\n"
        "    return helper()\n"
    )
    parser = CodeParser(code)
    parser.parse_code()
    assert parser.exec_method('main') == 2


def test_global_constants_keep_their_real_types():
    code = (
        "MAX_TURNS = 200\n"
        "SILENCE_DUR = 0.8\n"
        "TAG = 'meeting'\n"
        "ENABLED = False\n"
        "\n"
        "def main():\n"
        "    assert len([1]) < MAX_TURNS\n"
        "    assert SILENCE_DUR + 0.2 == 1.0\n"
        "    assert TAG == 'meeting'\n"
        "    return ENABLED\n"
    )
    parser = CodeParser(code)
    parser.parse_code()
    assert parser.exec_globals['MAX_TURNS'] == 200
    assert parser.exec_globals['SILENCE_DUR'] == 0.8
    assert parser.exec_globals['TAG'] == 'meeting'
    assert parser.exec_globals['ENABLED'] is False
    assert parser.exec_method('main') is False
