"""Tests for scan_sibling_guard_consistency.py.

Includes the real bug shape (danzin's confirmed #5) and the two documented
false-positive patterns found while validating against the real checkout
(close_w's legitimate idempotent-close exception, read1_w's guard-via-
delegation).
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "pypy-review-toolkit" / "scripts"))

from scan_sibling_guard_consistency import _check_file  # noqa: E402


def _write(tmp_path: Path, source: str) -> Path:
    f = tmp_path / "sample.py"
    f.write_text(textwrap.dedent(source))
    return f


def test_missing_guard_is_flagged_when_convention_established(tmp_path):
    # The real shape: N-1 of N exposed methods guard, one doesn't.
    src = """
        class W_Wrapper(object):
            def read_w(self, space):
                self._check_attached(space)
                return self.buf

            def write_w(self, space, data):
                self._check_attached(space)
                self.buf = data

            def flush_w(self, space):
                self._check_attached(space)

            def _dealloc_warn_w(self, space, msg):
                return self.buf

        W_Wrapper.typedef = TypeDef(
            "Wrapper",
            read = interp2app(W_Wrapper.read_w),
            write = interp2app(W_Wrapper.write_w),
            flush = interp2app(W_Wrapper.flush_w),
            _dealloc_warn = interp2app(W_Wrapper._dealloc_warn_w),
        )
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert len(findings) == 1
    assert findings[0]["type"] == "sibling-guard-missing"
    assert "_dealloc_warn_w" in findings[0]["message"]


def test_no_findings_when_convention_not_established(tmp_path):
    # W_IOBase shape: minority of methods use a guard -- not a real
    # convention to deviate from.
    src = """
        class W_Base(object):
            def read_w(self, space):
                return self.buf

            def write_w(self, space, data):
                self.buf = data

            def flush_w(self, space):
                pass

            def isatty_w(self, space):
                self._check_closed(space)
                return False

        W_Base.typedef = TypeDef(
            "Base",
            read = interp2app(W_Base.read_w),
            write = interp2app(W_Base.write_w),
            flush = interp2app(W_Base.flush_w),
            isatty = interp2app(W_Base.isatty_w),
        )
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_no_findings_when_no_exposed_methods(tmp_path):
    src = """
        class Plain(object):
            def read_w(self, space):
                return self.buf

            def write_w(self, space, data):
                self.buf = data
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_init_and_descr_init_excluded_from_evaluation(tmp_path):
    # __init__/descr_init legitimately have nothing to guard against yet.
    src = """
        class W_Thing(object):
            def descr_init(self, space):
                self.buf = None

            def read_w(self, space):
                self._check_ready(space)
                return self.buf

            def write_w(self, space, data):
                self._check_ready(space)
                self.buf = data

        W_Thing.typedef = TypeDef(
            "Thing",
            __init__ = interp2app(W_Thing.descr_init),
            read = interp2app(W_Thing.read_w),
            write = interp2app(W_Thing.write_w),
        )
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []
