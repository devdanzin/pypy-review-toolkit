"""Tests for scan_immutability_contracts.py.

Includes regression fixtures for two real bugs found while validating this
scanner against the actual pypy/pypy checkout (not synthetic examples
alone -- both of these were caught by running the scanner for real and
checking a surprising result):

1. ``jit.promote()`` proximity must be class-wide, not scoped to the
   mutating method -- ``CPPMethod.cif_descr``'s ``jit.promote(self)`` call
   is in a different method than the one that reassigns the field.
2. The promoted argument must actually be ``self`` -- an earlier version
   matched ``jit.promote(frame.last_instr)`` inside ``GeneratorIterator``
   and wrongly tiered it as "strongest evidence" alongside the genuine
   ``cif_descr`` case.
"""

import ast
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "pypy-review-toolkit" / "scripts"))

from scan_immutability_contracts import _check_file, _mentions_jit_promote  # noqa: E402


def _write(tmp_path: Path, source: str) -> Path:
    f = tmp_path / "sample.py"
    f.write_text(textwrap.dedent(source))
    return f


def test_strictly_immutable_field_reassigned_is_flagged(tmp_path):
    src = """
        class Foo(object):
            _immutable_fields_ = ['x']

            def __init__(self):
                self.x = 1

            def reset(self):
                self.x = 2
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert len(findings) == 1
    assert findings[0]["type"] == "immutability-contract-mismatch"


def test_quasi_immutable_field_reassigned_is_not_flagged(tmp_path):
    # The '?' marker means "assigned at most once after construction" is
    # expected -- this must NOT be flagged.
    src = """
        class Foo(object):
            _immutable_fields_ = ['x?']

            def __init__(self):
                self.x = None

            def finish_setup(self):
                self.x = compute()
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_quasi_immutable_list_marker_ordering_not_flagged(tmp_path):
    # THE fixture: 'closure?[*]' -- '?' before '[*]'. Real PyPy convention.
    src = """
        class Foo(object):
            _immutable_fields_ = ['closure?[*]']

            def __init__(self):
                self.closure = None

            def bind(self, cells):
                self.closure = cells
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_del_only_reassignment_is_lower_priority(tmp_path):
    src = """
        class Foo(object):
            _immutable_fields_ = ['buf']

            def __init__(self):
                self.buf = allocate()

            def __del__(self):
                self.buf = None
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert len(findings) == 1
    assert findings[0]["type"] == "immutability-contract-mismatch-cleanup"
    assert findings[0]["confidence"] == "low"


def test_promote_self_in_different_method_is_strongest_tier(tmp_path):
    # Regression fixture for bug #1: jit.promote(self) in a DIFFERENT
    # method than the one that reassigns the field -- must still be
    # detected, matching the real CPPMethod.cif_descr shape.
    src = """
        class Foo(object):
            _immutable_fields_ = ['cache']

            def __init__(self):
                self.cache = None

            def build(self):
                self.cache = compute()

            def call(self):
                jit.promote(self)
                return self.cache
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert len(findings) == 1
    assert findings[0]["type"] == "immutability-contract-mismatch-promoted"
    assert findings[0]["confidence"] == "high"


def test_promote_of_unrelated_attribute_is_not_strongest_tier(tmp_path):
    # Regression fixture for bug #2: jit.promote(frame.last_instr) is NOT
    # jit.promote(self) -- must not be tiered as strongest evidence.
    src = """
        class Foo(object):
            _immutable_fields_ = ['cache']

            def __init__(self):
                self.cache = None

            def build(self):
                self.cache = compute()

            def call(self, frame):
                jit.promote(frame.last_instr)
                return self.cache
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert len(findings) == 1
    assert findings[0]["type"] == "immutability-contract-mismatch"
    assert findings[0]["confidence"] == "medium"


def test_mentions_jit_promote_requires_self_argument():
    tree = ast.parse("def f(self, frame):\n    jit.promote(frame.last_instr)\n")
    func = tree.body[0]
    assert _mentions_jit_promote(func) is False


def test_mentions_jit_promote_detects_self():
    tree = ast.parse("def f(self):\n    jit.promote(self)\n")
    func = tree.body[0]
    assert _mentions_jit_promote(func) is True


def test_no_immutable_fields_declaration_no_findings(tmp_path):
    src = """
        class Foo(object):
            def __init__(self):
                self.x = 1

            def reset(self):
                self.x = 2
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []


def test_mutation_only_in_init_no_findings(tmp_path):
    src = """
        class Foo(object):
            _immutable_fields_ = ['x']

            def __init__(self):
                self.x = 1
                self.x = 2
    """
    f = _write(tmp_path, src)
    findings = _check_file(f, tmp_path)
    assert findings == []
