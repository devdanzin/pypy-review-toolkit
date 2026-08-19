"""Tests for scan_translated_divergence.py."""

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

from scan_translated_divergence import _classify_two_arm  # noqa: E402


def _body(source: str) -> list[ast.stmt]:
    return ast.parse(textwrap.dedent(source)).body


def test_translation_boundary_llop_is_not_fix():
    if_body = _body(
        """
        llop.debug_fatalerror(lltype.Void, msg)
        """
    )
    else_body = _body(
        """
        raise AssertGreenFailed(msg)
        """
    )

    classification, reason = _classify_two_arm(if_body, else_body)

    assert classification == "CONSIDER"
    assert "translation-boundary" in reason


def test_translation_boundary_nontranslated_helper_is_not_fix():
    if_body = _body(
        """
        raise ContinueRunningNormally(args)
        """
    )
    else_body = _body(
        """
        self._nontranslated_run_directly(args, loop_token)
        assert 0
        """
    )

    classification, reason = _classify_two_arm(if_body, else_body)

    assert classification == "CONSIDER"
    assert "translation-boundary" in reason


def test_translation_boundary_debug_print_traceback_is_not_fix():
    if_body = _body(
        """
        llop.debug_print_traceback(lltype.Void)
        """
    )
    else_body = _body(
        """
        raise
        """
    )

    classification, reason = _classify_two_arm(if_body, else_body)

    assert classification == "CONSIDER"
    assert "translation-boundary" in reason


def test_unrecognized_control_flow_difference_remains_fix():
    if_body = _body(
        """
        return value
        """
    )
    else_body = _body(
        """
        do_something()
        """
    )

    classification, reason = _classify_two_arm(if_body, else_body)

    assert classification == "FIX"
    assert reason == "arms have inconsistent return/raise control-flow shape"
