"""RPython-aware parsing and semantic helpers shared by pypy-review-toolkit scanners.

RPython source targets Python 2.7 syntax. Python 3's ``ast.parse()`` fails on
roughly 17% of real files in the PyPy checkout (print/exec statements without
parens, leading-zero octal/long-int literals, parenthesized tuple-unpacking
function parameters, etc. -- see ``pypy-review-toolkit-ast-trial-results.md``
for the full trial). This module provides the fallback: try ``ast.parse()``
first (cheap, and gives real ``ast.AST`` nodes for the ~83% of files that
parse cleanly), fall back to tree-sitter-python on ``SyntaxError``.

Every scanner should call ``parse_rpython_file()`` here rather than
``scan_common.parse_source()`` directly -- ``scan_common.parse_source()``
returns ``None`` outright on the 17% of files that need the fallback, which
would make those files invisible to every scanner, including the ones
targeting ``rpython/rlib/jit.py`` itself (one of the files that needs the
fallback).

Also home to the RPython-specific semantic helpers scanners need that vanilla
``ast`` scanning has no reason to know about: decorator detection,
``_immutable_fields_``/``_attrs_`` classification (including the easy-to-get-
wrong ``'field?[*]'`` quasi-immutable marker ordering -- see design doc §3.2),
and ``we_are_translated()`` predicate detection.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tree_sitter import Language, Parser
    import tree_sitter_python as tspython

    _TS_LANGUAGE = Language(tspython.language())
    _TS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when the optional dep is missing
    _TS_AVAILABLE = False


# RPython class attributes the annotator/JIT give special meaning to. A
# "class-level-mutable-attribute"-style check that doesn't know about these
# will flood a real review with false positives on idiomatic RPython -- see
# design doc §1's transfer-gradient table and the round-4 investigation.
RPYTHON_MAGIC_CLASS_ATTRS = frozenset(
    {
        "_immutable_fields_",
        "_immutable_",
        "_attrs_",
        "_settled_",
        "_virtualizable_",
    }
)


@dataclass
class ParseResult:
    """Uniform result of parsing one RPython source file.

    ``parser`` is one of ``"ast"``, ``"tree_sitter"``, or ``"failed"``.
    ``ast_tree`` is populated only when ``parser == "ast"``.
    ``ts_tree`` is populated only when ``parser == "tree_sitter"``.
    ``has_errors`` is only meaningful for the tree-sitter path -- it means
    the tree parsed but contains one or more local ERROR nodes (partial
    recovery, per the AST trial's 44-file "has_errors" bucket). A tree-sitter
    result with ``has_errors=True`` still carries real, usable structure for
    most files -- see the trial doc's ``rpython/rlib/jit.py`` example, which
    still recovers 45 functions and 24 classes despite one local error node.
    """

    path: Path
    source: str
    parser: str
    ast_tree: ast.Module | None = None
    ts_tree: Any = None
    has_errors: bool = False


def parse_rpython_file(path: Path) -> ParseResult:
    """Parse *path*, trying ``ast.parse()`` first and falling back to tree-sitter.

    Returns a ``ParseResult`` with ``parser == "failed"`` only when the file
    can't be read at all, or when both parsers are unavailable/fail --
    tree-sitter-python had zero hard failures across all 443 files that broke
    ``ast.parse()`` in the trial, so a genuine "failed" result should be rare.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ParseResult(path=path, source="", parser="failed")

    try:
        tree = ast.parse(source, filename=str(path))
        return ParseResult(path=path, source=source, parser="ast", ast_tree=tree)
    except (SyntaxError, ValueError, RecursionError):
        pass

    if not _TS_AVAILABLE:
        return ParseResult(path=path, source=source, parser="failed")

    parser = Parser(_TS_LANGUAGE)
    try:
        ts_tree = parser.parse(source.encode("utf-8"))
    except Exception:  # pragma: no cover - tree-sitter had zero hard failures in the trial
        return ParseResult(path=path, source=source, parser="failed")

    return ParseResult(
        path=path,
        source=source,
        parser="tree_sitter",
        ts_tree=ts_tree,
        has_errors=ts_tree.root_node.has_error,
    )


def find_decorators(func_node: ast.FunctionDef) -> list[str]:
    """Return the dotted names of *func_node*'s decorators, e.g. ``["jit.elidable"]``.

    Only handles the ``ast`` path -- callers on the tree-sitter path need
    the tree-sitter-native equivalent, not yet implemented (v0.1 scanners
    default to the 83% ``ast``-parseable surface for decorator-sensitive
    checks; the ~17% tree-sitter fallback surface is covered for structural
    checks like function/class enumeration but not yet for decorator
    inspection).
    """
    names = []
    for dec in func_node.decorator_list:
        node = dec
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        if parts:
            names.append(".".join(reversed(parts)))
    return names


def classify_immutable_field(raw: str) -> tuple[str, bool]:
    """Classify one ``_immutable_fields_`` entry.

    Returns ``(base_field_name, is_quasi_immutable)``. PyPy's quasi-immutable
    marker convention is a ``?`` character, which can appear either at the
    very end (``'is_enabled?'``) or *before* a trailing ``[*]`` list marker
    (``'closure?[*]'``) -- the ordering matters and is easy to get wrong: an
    earlier version of the investigation behind this toolkit's design doc
    only checked for a trailing ``?`` and mis-classified ``'closure?[*]'`` as
    fully immutable, producing false positives. This function checks for
    ``?`` anywhere in the raw string, which is the corrected behavior.
    """
    is_quasi = "?" in raw
    base = raw.replace("?", "").split("[")[0]
    return base, is_quasi


def is_we_are_translated_call(test_node: ast.expr) -> tuple[bool, bool]:
    """Detect ``we_are_translated()`` / ``not we_are_translated()`` as an ``if`` test.

    Returns ``(is_match, is_negated)``. Does not currently resolve import
    aliasing (``from rpython.rlib.objectmodel import we_are_translated as
    wat``) -- the census behind this toolkit's design doc did not encounter
    that pattern in the real checkout, but a future version should verify
    the name actually resolves to ``rpython.rlib.objectmodel.we_are_translated``
    rather than matching on the bare name alone.
    """
    node = test_node
    negated = False
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        negated = True
        node = node.operand
    is_match = (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "we_are_translated"
    )
    return is_match, negated


def is_debug_only_body(body: list[ast.stmt]) -> bool:
    """True if *body* consists only of ``assert``/``raise`` statements.

    This is the flat single-arm ``if not we_are_translated(): assert ...``
    shape. **Correction, found by running the built scanner against the
    real checkout rather than trusting the earlier hand-sampled estimate**:
    a first version of this toolkit's design doc claimed this shape was
    "the dominant real shape... every sampled instance" was this pattern,
    generalizing from ~5-8 manually inspected sites. Running the actual
    scanner against all 226 real single-arm sites found only 33 (15%) match
    this flat shape -- the hand sample happened to draw disproportionately
    from files with "debug" in the name/purpose (``rpython/rlib/debug.py``,
    ``rpython/rtyper/debug.py``), which biased the sample. See
    ``is_debug_adjacent_body`` for the broadened check that also catches
    two more real shapes found this way; even combined, only ~22% of real
    single-arm sites are recognizably debug-only-adjacent. The remaining
    ~78% need real per-site attention, not blanket suppression -- a
    materially different picture than the original estimate.
    """
    if not body:
        return False
    return all(isinstance(stmt, (ast.Assert, ast.Raise)) for stmt in body)


def is_debug_adjacent_body(body: list[ast.stmt], negated: bool) -> bool:
    """Broadened debug-only-adjacent check, covering three shapes found by
    running the flat ``is_debug_only_body`` check against the real checkout
    and manually inspecting what it missed:

    1. The flat ``assert``/``raise``-only shape (``is_debug_only_body``).
    2. Asserts wrapped in a simple ``for``/``if`` control structure, e.g.
       ``rpython/memory/gc/minimarkpage.py``'s
       ``if not we_are_translated(): for a in ...: assert a.nfreepages == 0``.
    3. The early-return idiom ``if we_are_translated(): return`` immediately
       preceding an assertion the translated build skips --
       ``pypy/interpreter/pyframe.py``'s ``assert_stack_index`` is the
       clearest example. Semantically the same intent as the negated flat
       shape, phrased inverted.

    Even with all three, real coverage on the full census was only ~22%
    (50/226) -- most single-arm sites are still genuinely something else
    and this function should not be treated as "solves" the suppression
    problem, only as a documented partial improvement over the flat check
    alone.
    """
    if is_debug_only_body(body):
        return True
    if not negated and len(body) <= 2 and any(isinstance(s, ast.Return) for s in body):
        return True
    if body and all(isinstance(s, (ast.For, ast.If)) for s in body):
        for s in body:
            inner_body = s.body
            if not all(isinstance(x, (ast.Assert, ast.Raise)) for x in inner_body):
                return False
        return True
    return False


def ts_top_level_defs(ts_tree: Any) -> tuple[int, int]:
    """Count top-level function and class definitions in a tree-sitter tree.

    Used to sanity-check how much structure survives on the ~17% of files
    that need the tree-sitter fallback -- e.g. confirming that
    ``rpython/rlib/jit.py`` (which has a local ERROR node) still yields its
    real 45 functions / 24 classes despite the parse error, per the AST
    trial.
    """
    root = ts_tree.root_node
    n_func = sum(1 for c in root.children if c.type in ("function_definition", "decorated_definition"))
    n_class = sum(1 for c in root.children if c.type == "class_definition")
    return n_func, n_class
