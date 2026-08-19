"""Tests for scan_immutability_contracts.py."""

import ast
import sys
import textwrap
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "plugins"
        / "pypy-review-toolkit"
        / "scripts"
    ),
)

from scan_immutability_contracts import _is_lazy_initialized_field  # noqa: E402


def _class(source: str) -> ast.ClassDef:
    tree = ast.parse(textwrap.dedent(source))
    return tree.body[0]


def test_null_initialized_field_with_guard_is_lazy_init():
    node = _class(
        """
        class CPPMethod:
            def __init__(self):
                self.cif_descr = None

            def _setup(self):
                if self.cif_descr is None:
                    self.cif_descr = build()
        """
    )

    assert _is_lazy_initialized_field(node, "cif_descr", ["_setup"])


def test_nullptr_initialized_field_with_guard_is_lazy_init():
    node = _class(
        """
        class CPPMethod:
            def __init__(self):
                self.cif_descr = lltype.nullptr(CIF_DESCRIPTION)

            def _setup(self):
                if self.cif_descr == lltype.nullptr(CIF_DESCRIPTION):
                    self.cif_descr = build()
        """
    )

    assert _is_lazy_initialized_field(node, "cif_descr", ["_setup"])


def test_unrelated_mutation_is_not_lazy_init():
    node = _class(
        """
        class Example:
            def __init__(self):
                self.value = None

            def update(self):
                self.value = compute()
        """
    )

    assert not _is_lazy_initialized_field(node, "value", ["update"])


def test_non_null_initialization_is_not_lazy_init():
    node = _class(
        """
        class Example:
            def __init__(self):
                self.value = initial()

            def update(self):
                if self.value is None:
                    self.value = compute()
        """
    )

    assert not _is_lazy_initialized_field(node, "value", ["update"])
