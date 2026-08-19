"""Tests for pypy_utils.py.

Includes the specific ``'field?[*]'``-shaped fixture the design doc's
execution plan (§8, step 6) called for explicitly: PyPy's quasi-immutable
list-field marker puts ``?`` *before* ``[*]``, and an earlier version of the
investigation behind this toolkit mis-classified that shape as fully
immutable because it only checked for a trailing ``?``. This test exists so
that mistake can't silently come back.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "pypy-review-toolkit" / "scripts"))

from pypy_utils import (  # noqa: E402
    classify_immutable_field,
    find_decorators,
    is_debug_only_body,
    is_we_are_translated_call,
    parse_rpython_file,
)


def test_classify_immutable_field_plain():
    assert classify_immutable_field("co_filename") == ("co_filename", False)


def test_classify_immutable_field_trailing_marker():
    assert classify_immutable_field("mstrategy?") == ("mstrategy", True)


def test_classify_immutable_field_list_marker_no_quasi():
    # A list field with no '?' at all -- fully immutable list.
    assert classify_immutable_field("co_cellvars[*]") == ("co_cellvars", False)


def test_classify_immutable_field_quasi_before_list_marker():
    # THE fixture: '?' comes before '[*]', not after. Real PyPy convention,
    # confirmed against pypy/interpreter/function.py's Function.closure.
    # A marker check that only looks at the string's tail (e.g.
    # `raw.endswith('?')`) will wrongly classify this as fully immutable.
    assert classify_immutable_field("closure?[*]") == ("closure", True)


def test_is_we_are_translated_call_positive():
    tree = ast.parse("if we_are_translated():\n    pass\n")
    if_node = tree.body[0]
    is_match, negated = is_we_are_translated_call(if_node.test)
    assert is_match is True
    assert negated is False


def test_is_we_are_translated_call_negated():
    tree = ast.parse("if not we_are_translated():\n    assert True\n")
    if_node = tree.body[0]
    is_match, negated = is_we_are_translated_call(if_node.test)
    assert is_match is True
    assert negated is True


def test_is_we_are_translated_call_negative():
    tree = ast.parse("if some_other_thing():\n    pass\n")
    if_node = tree.body[0]
    is_match, _ = is_we_are_translated_call(if_node.test)
    assert is_match is False


def test_is_debug_only_body_true():
    tree = ast.parse("if x:\n    assert y\n    raise ValueError('x')\n")
    body = tree.body[0].body
    assert is_debug_only_body(body) is True


def test_is_debug_only_body_false_has_assignment():
    # The rgil.py-style two-arm emulation shim shape: real assignment, not
    # just assert/raise -- must NOT be classified as the debug-only pattern.
    tree = ast.parse("if x:\n    y = compute()\n    assert y\n")
    body = tree.body[0].body
    assert is_debug_only_body(body) is False


def test_is_debug_only_body_empty():
    assert is_debug_only_body([]) is False


def test_find_decorators_dotted():
    tree = ast.parse("@jit.elidable\ndef f():\n    pass\n")
    func = tree.body[0]
    assert find_decorators(func) == ["jit.elidable"]


def test_find_decorators_multiple():
    tree = ast.parse("@jit.unroll_safe\n@staticmethod\ndef f():\n    pass\n")
    func = tree.body[0]
    assert find_decorators(func) == ["jit.unroll_safe", "staticmethod"]


def test_parse_rpython_file_ast_path(tmp_path):
    f = tmp_path / "ordinary.py"
    f.write_text("def f():\n    return 1\n")
    result = parse_rpython_file(f)
    assert result.parser == "ast"
    assert result.ast_tree is not None


def test_parse_rpython_file_tree_sitter_fallback(tmp_path):
    # print statement -- valid Python 2, a SyntaxError under Python 3's ast.
    f = tmp_path / "py2_only.py"
    f.write_text("def f():\n    print 'hello'\n")
    result = parse_rpython_file(f)
    # Falls back to tree-sitter if available; if the optional dependency
    # isn't installed in a given environment, "failed" is the honest result
    # rather than a silent False positive.
    assert result.parser in ("tree_sitter", "failed")


def test_parse_rpython_file_missing_file():
    result = parse_rpython_file(Path("/nonexistent/path/does_not_exist.py"))
    assert result.parser == "failed"
