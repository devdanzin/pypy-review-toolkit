"""Tests for scan_rpython_restrictions.py."""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "pypy-review-toolkit" / "scripts"))

from scan_rpython_restrictions import _check_file  # noqa: E402


def _write(tmp_path: Path, source: str) -> Path:
    f = tmp_path / "sample.py"
    f.write_text(textwrap.dedent(source))
    return f


def test_kwargs_function_is_flagged(tmp_path):
    src = """
        def f(a, b, **kwds):
            return kwds
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert len(findings) == 1
    assert findings[0]["type"] == "rpython-unbounded-kwargs"


def test_not_rpython_function_is_not_flagged(tmp_path):
    src = """
        def f(**kwds):
            \"\"\"NOT_RPYTHON\"\"\"
            return kwds
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_not_rpython_module_is_not_flagged(tmp_path):
    src = """
        # NOT_RPYTHON

        def f(**kwds):
            return kwds
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_function_without_kwargs_not_flagged(tmp_path):
    src = """
        def f(a, b):
            return a + b
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_eval_exec_not_flagged_at_all(tmp_path):
    # Deliberately excluded per the design doc's investigation -- 0 of 21
    # real candidates were genuine violations.
    src = """
        def f():
            exec("x = 1")
            return eval("1 + 1")
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_not_rpython_decorator_is_not_flagged(tmp_path):
    src = """
        def not_rpython(func):
            return func

        @not_rpython
        def f(**kwds):
            return kwds
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []
