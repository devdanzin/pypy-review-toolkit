"""Tests for scan_unvalidated_helper_calls.py.

Includes a regression fixture for the real bug found while validating this
scanner against the actual checkout: a call inside a nested function was
being processed once per enclosing function (including outer ones), so a
guard pattern belonging to a sibling nested function could wrongly clear an
unguarded call in a different nested function. Confirmed on real code:
pypy/objspace/std/newformat.py's nested _lit() function.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "pypy-review-toolkit" / "scripts"))

from scan_unvalidated_helper_calls import _check_file  # noqa: E402

_HELPERS = [
    {
        "qualname": "rutf8.unichr_as_utf8",
        "precondition": "must be <= 0x10FFFF",
        "guard_pattern_hints": ["0x10FFFF", "0x110000", "1114111"],
    }
]


def _write(tmp_path: Path, source: str) -> Path:
    f = tmp_path / "sample.py"
    f.write_text(textwrap.dedent(source))
    return f


def test_unguarded_call_is_flagged_consider(tmp_path):
    src = """
        def append_utf8(self, value):
            w_ch = self.space.newutf8(rutf8.unichr_as_utf8(r_uint(value)), 1)
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path, _HELPERS)
    assert len(findings) == 1
    assert findings[0]["type"] == "unvalidated-helper-call"
    assert findings[0]["classification"] == "CONSIDER"


def test_guarded_call_is_flagged_acceptable(tmp_path):
    src = """
        def unichr(space, code):
            if code < 0 or code > 0x10FFFF:
                raise oefmt(space.w_ValueError, "unichr() arg out of range")
            s = rutf8.unichr_as_utf8(code, allow_surrogates=True)
            return space.newutf8(s, 1)
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path, _HELPERS)
    assert len(findings) == 1
    assert findings[0]["type"] == "unvalidated-helper-call-guarded"
    assert findings[0]["classification"] == "ACCEPTABLE"


def test_nested_function_guard_does_not_leak_to_sibling(tmp_path):
    # Regression fixture: a guard in one nested function must NOT clear an
    # unguarded call in a different nested function of the same outer
    # function -- both nested functions get walked when the outer function
    # is processed, and an earlier version of this scanner scoped calls to
    # every enclosing function, not just the innermost one.
    src = """
        class Foo(object):
            def outer(self):
                def _guarded(code):
                    if code < 0 or code > 0x10FFFF:
                        raise ValueError("bad")
                    return rutf8.unichr_as_utf8(code)

                def _unguarded(s):
                    return rutf8.unichr_as_utf8(ord(s[0]))

                return _guarded, _unguarded
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path, _HELPERS)
    assert len(findings) == 2
    by_type = {f["type"] for f in findings}
    assert by_type == {"unvalidated-helper-call", "unvalidated-helper-call-guarded"}
    # Exactly one of each -- not two of the same type, which is what the
    # bug produced (the unguarded call got double-counted: once correctly
    # as unguarded via its own scope, once incorrectly as guarded via the
    # outer function's broader scope, which contained _guarded's check text).
    guarded = [f for f in findings if f["type"] == "unvalidated-helper-call-guarded"]
    unguarded = [f for f in findings if f["type"] == "unvalidated-helper-call"]
    assert len(guarded) == 1
    assert len(unguarded) == 1


def test_call_with_no_matching_helper_not_flagged(tmp_path):
    src = """
        def f():
            return some_other_function(1, 2, 3)
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path, _HELPERS)
    assert findings == []


def test_bare_imported_name_form_also_matches(tmp_path):
    # from rpython.rlib.rutf8 import unichr_as_utf8 -- bare name, not dotted.
    src = """
        def f(code):
            return unichr_as_utf8(code)
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path, _HELPERS)
    assert len(findings) == 1
    assert findings[0]["type"] == "unvalidated-helper-call"
