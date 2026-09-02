---
description: "Cross-reference a fresh scan against the known-bugs catalog"
argument-hint: "[scope]"
allowed-tools: ["Bash", "Glob", "Grep", "Read"]
---

# PyPy Review Toolkit — Known Issues

Cross-reference current scanner output against `data/pypy_known_bugs.tsv`.

**Scope:** "$ARGUMENTS" (default: entire in-scope surface)

## Status

`data/pypy_known_bugs.tsv` holds **16 fusil-confirmed bugs**
(`PYPY-FUZZ-001..016`), all reproduced on PyPy 7.3.23 (`194f9f44b505`) with
CPython 3.14.3 as the differential oracle. The full catalog -- reduced
reproducers, captured evidence from both interpreters, and per-finding analysis
-- lives in [`pypy-findings`](https://github.com/devdanzin/pypy-findings).

Four of the fifteen (`006`, `007`, `009`, `010`) need the process near an
address-space limit and are invisible without one; three (`001`, `008`, `009`)
are not statically checkable from RPython source at all, and are recorded for
known-issues tracking rather than as scanner targets. The ones this toolkit's
scanners CAN see are the sibling-guard shapes -- `005`, `011`, `013`, `014` --
which are four instances of one defect: a member that skips an invalidation or
initialization guard its siblings all apply.

## Workflow

1. Run all four scanners.
2. Load `data/pypy_known_bugs.tsv`. Several rows are deliberately not
   scanner-visible (`file` is `n/a` or names a C-generated parser): report
   those as tracked-but-unscannable rather than as absent, so an empty scanner
   match never reads as a clean bill of health.
3. For each catalog entry (once seeded): check whether the corresponding
   scanner still finds it (`present`), whether the file/line has drifted
   (`line_drifted`), whether the containing function is gone
   (`absent_in_function`), or whether it's genuinely fixed (`absent`).

## Usage

```
/pypy-review-toolkit:known-issues
```
