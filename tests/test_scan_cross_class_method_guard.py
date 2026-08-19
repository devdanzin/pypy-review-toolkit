"""Tests for scan_cross_class_method_guard.py.

Includes the confirmed real bug (#5) as a regression fixture, plus fixtures
for the two real noise problems found while validating against the actual
(correct-version) checkout: __init__ appearing on nearly every class
produces near-total noise without a name exclusion list, and directory-wide
grouping alone still conflates unrelated classes that happen to implement
the same generic Python protocol method (__contains__, close, get) -- fixed
by restricting to PyPy's own `_w`-suffixed interp2app naming convention.
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "pypy-review-toolkit" / "scripts"))

import scan_cross_class_method_guard as scanner  # noqa: E402


def _write_files(tmp_path: Path, files: dict[str, str]) -> list[Path]:
    paths = []
    for name, source in files.items():
        f = tmp_path / name
        f.write_text(textwrap.dedent(source))
        paths.append(f)
    return paths


def _run(tmp_path: Path, files: dict[str, str]) -> list[dict]:
    import ast

    all_methods = []
    for name, source in files.items():
        tree = ast.parse(textwrap.dedent(source))
        all_methods.extend(scanner._collect_methods(tree, name))

    by_dir_and_name: dict[tuple, list] = {}
    for m in all_methods:
        if m["method_name"] in scanner._EXCLUDED_METHOD_NAMES:
            continue
        if not m["method_name"].endswith("_w"):
            continue
        directory = str(Path(m["file"]).parent)
        by_dir_and_name.setdefault((directory, m["method_name"]), []).append(m)

    findings = []
    for (directory, method_name), defs in by_dir_and_name.items():
        distinct_classes = {(d["class_name"], d["file"]) for d in defs}
        if len(distinct_classes) < 2:
            continue
        guarded_defs = []
        unguarded_defs = []
        for d in defs:
            method = d["node"]
            if scanner._is_trivial_stub(method):
                continue
            accessed = scanner._self_fields_accessed(method)
            if not accessed:
                continue
            guarded = scanner._guarded_fields(method)
            if accessed & guarded:
                guarded_defs.append(d)
            else:
                unguarded_defs.append(d)
        if not guarded_defs or not unguarded_defs:
            continue
        for d in unguarded_defs:
            findings.append({"class_name": d["class_name"], "method_name": method_name, "file": d["file"]})
    return findings


def test_confirmed_bug_shape_is_caught(tmp_path):
    # Simplified version of the real bug: 3 sibling classes' same-named
    # method guard their field, one doesn't.
    files = {
        "a.py": """
            class W_BufferedReader(object):
                def _dealloc_warn_w(self, space, w_source):
                    if self.w_raw:
                        space.call_method(self.w_raw, "_dealloc_warn", w_source)
        """,
        "b.py": """
            class W_FileIO(object):
                def _dealloc_warn_w(self, space, w_source):
                    if self.fd >= 0:
                        pass
        """,
        "c.py": """
            class W_TextIOWrapper(object):
                def _dealloc_warn_w(self, space, w_source):
                    space.call_method(self.w_buffer, "_dealloc_warn", w_source)
        """,
    }
    findings = _run(tmp_path, files)
    assert len(findings) == 1
    assert findings[0]["class_name"] == "W_TextIOWrapper"
    assert findings[0]["method_name"] == "_dealloc_warn_w"


def test_init_excluded_even_across_unrelated_classes(tmp_path):
    # Regression: __init__ must never be compared across unrelated classes.
    files = {
        "a.py": """
            class Alpha(object):
                def __init__(self, space):
                    if self.ready:
                        self.x = 1
        """,
        "b.py": """
            class Beta(object):
                def __init__(self, space):
                    self.y = 2
        """,
    }
    findings = _run(tmp_path, files)
    assert findings == []


def test_generic_protocol_method_not_compared_across_unrelated_classes(tmp_path):
    # Regression: bare 'close' (no _w suffix) must not trigger comparison
    # between unrelated classes -- this is what produced 666 false
    # positives before the _w-suffix restriction was added.
    files = {
        "a.py": """
            class Context(object):
                def close(self, space):
                    if self.active:
                        pass
        """,
        "b.py": """
            class Dbm(object):
                def close(self, space):
                    self.db.close()
        """,
    }
    findings = _run(tmp_path, files)
    assert findings == []


def test_w_suffixed_method_still_compared_when_genuinely_related(tmp_path):
    files = {
        "a.py": """
            class W_BytesIO(object):
                def close_w(self, space):
                    if self._alive:
                        self.close()
        """,
        "b.py": """
            class W_StringIO(object):
                def close_w(self, space):
                    self.close()
        """,
    }
    findings = _run(tmp_path, files)
    assert len(findings) == 1
    assert findings[0]["class_name"] == "W_StringIO"


def test_single_definition_no_comparison(tmp_path):
    files = {
        "a.py": """
            class Solo(object):
                def read_w(self, space):
                    return self.buf
        """,
    }
    findings = _run(tmp_path, files)
    assert findings == []


def test_trivial_stub_not_flagged(tmp_path):
    files = {
        "a.py": """
            class W_IOBase(object):
                def close_w(self, space):
                    pass
        """,
        "b.py": """
            class W_BytesIO(object):
                def close_w(self, space):
                    if self._alive:
                        self.close()
        """,
        "c.py": """
            class W_StringIO(object):
                def close_w(self, space):
                    self.close()
        """,
    }
    findings = _run(tmp_path, files)
    # W_IOBase's stub isn't flagged; W_StringIO (genuinely unguarded, real
    # field access) is.
    assert len(findings) == 1
    assert findings[0]["class_name"] == "W_StringIO"


def test_close_w_is_exempt_from_cross_class_guard_comparison(tmp_path):
    files = {
        "a.py": """
            class W_BytesIO(object):
                def close_w(self, space):
                    self.close()
        """,
        "b.py": """
            class W_TextIOWrapper(object):
                def close_w(self, space):
                    self._check_closed(space)
                    self.close()
        """,
    }
    findings = _run(tmp_path, files)
    assert findings == []


def test_abstract_class_is_recognized_as_base_or_mixin():
    assert scanner._is_base_or_mixin_class("W_AbstractBuffer")
    assert scanner._is_base_or_mixin_class("W_IOBase")
    assert scanner._is_base_or_mixin_class("W_TextIOBase")
    assert scanner._is_base_or_mixin_class("BufferedMixin")
    assert not scanner._is_base_or_mixin_class("W_MemoryView")
