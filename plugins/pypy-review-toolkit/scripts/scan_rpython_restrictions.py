#!/usr/bin/env python3
"""Detect RPython-restriction violations -- narrowed scope, per design doc §3.5.

RPython forbids or restricts several constructs that are valid Python:
unbounded ``**kwargs``, ``eval``/``exec``, mixed-type containers, generators
crossing the translation boundary, and more. This is necessarily an
*approximation* of what the real annotator would reject -- the annotator
itself is the ground truth, and running it takes minutes, not seconds,
which is the whole reason a fast static approximation is useful at all.

**`eval`/`exec` are deliberately NOT checked here.** The investigation
behind this toolkit's design doc traced every one of the 21 real
inside-function `eval`/`exec` candidates in the checkout individually.
*Zero* were genuine runtime RPython-restriction violations -- all fell
under either the recognized codegen idiom (generate a specialized method
once via `exec()` at class-build time, before the annotator ever sees the
result) or build/translation-tooling reading a spec string, neither of
which is translated runtime code. Checking `eval`/`exec` here would be
pure noise on the real checkout; it's omitted rather than shipped
non-functional.

**Only `**kwargs` is checked in this version.** Mixed-type containers and
generators crossing the translation boundary are real restriction classes
per the design doc's surface catalogue, but neither has been censused yet
-- implementing a heuristic for either without first checking it against
real code risks repeating the same overclaiming mistake the flagship
scanner's original single-arm suppress rule made. Shipping `**kwargs` only,
honestly labeled, is preferred over shipping unvalidated heuristics for
the others.

Usage:
    python scan_rpython_restrictions.py [path] [--max-files N]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scan_common import (  # noqa: E402
    collect_python_files,
    deduplicate_findings,
    emit,
    find_project_root,
    parse_common_args,
    relative_to_root,
)
from pypy_utils import parse_rpython_file  # noqa: E402
from discover_pypy import build_pypy_envelope, discover  # noqa: E402


def _finding(
    ftype: str,
    classification: str,
    confidence: str,
    node: ast.AST,
    message: str,
    detail: str = "",
) -> dict:
    return {
        "type": ftype,
        "classification": classification,
        "confidence": confidence,
        "line": getattr(node, "lineno", 0),
        "column": getattr(node, "col_offset", 0),
        "message": message,
        "detail": detail,
    }


def _check_file(path: Path, project_root: Path) -> list[dict]:
    result = parse_rpython_file(path)
    if result.parser != "ast" or result.ast_tree is None:
        return []

    tree = result.ast_tree
    rel = relative_to_root(path, project_root)
    findings: list[dict] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.args.kwarg is not None:
            findings.append(
                _finding(
                    "rpython-unbounded-kwargs",
                    "CONSIDER",
                    "low",
                    node,
                    f"{node.name}() accepts **{node.args.kwarg.arg} -- RPython's annotator "
                    f"generally requires statically-known argument shapes; unbounded "
                    f"**kwargs is a common restriction violation, though not every instance "
                    f"is necessarily reachable from translated code (this scanner can't "
                    f"tell whether the function itself is translation-time tooling vs. "
                    f"runtime interpreter code -- see discover_pypy.py's layer "
                    f"classification for a coarser version of that distinction)",
                )
            )

    for f in findings:
        f["file"] = rel
    return findings


def analyze(target: str, *, max_files: int = 0) -> dict:
    resolved = Path(target).resolve()
    project_root = find_project_root(resolved)
    scan_root = resolved

    discovery = discover(target)

    if discovery.get("is_pypy_checkout"):
        checkout_root = Path(discovery["checkout_root"])
        files_by_layer = discovery.get("files_by_layer", {})
        in_scope_rel_paths = [
            rel
            for layer in sorted(discovery.get("in_scope_layers", []))
            for rel in files_by_layer.get(layer, [])
        ]
        files = [checkout_root / rel for rel in in_scope_rel_paths]
        files_total = len(files)
        if max_files > 0 and files_total > max_files:
            files = files[:max_files]
    else:
        files, files_total = collect_python_files(scan_root, max_files)

    non_test_files = [f for f in files if "/test/" not in str(f) and "/tests/" not in str(f)]

    findings: list[dict] = []
    for path in non_test_files:
        findings.extend(_check_file(path, project_root))

    findings = deduplicate_findings(findings)
    envelope = build_pypy_envelope(discovery, findings, functions_analyzed=len(non_test_files))
    envelope["pypy_info"]["restriction_checks_implemented"] = ["unbounded-kwargs"]
    envelope["pypy_info"]["restriction_checks_not_yet_implemented"] = [
        "mixed-type-containers",
        "generators-crossing-translation-boundary",
    ]
    envelope["pypy_info"]["eval_exec_deliberately_excluded"] = (
        "traced individually during investigation; 0 of 21 real candidates were genuine "
        "violations, all fell under the recognized codegen/build-tooling idiom"
    )
    return envelope


def main() -> None:
    target, max_files = parse_common_args(sys.argv[1:])
    result = analyze(target, max_files=max_files)
    emit(result)


if __name__ == "__main__":
    main()
