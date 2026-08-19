---
description: "Cross-reference a fresh scan against the known-bugs catalog"
argument-hint: "[scope]"
allowed-tools: ["Bash", "Glob", "Grep", "Read"]
---

# PyPy Review Toolkit — Known Issues

Cross-reference current scanner output against `data/pypy_known_bugs.tsv`.

**Scope:** "$ARGUMENTS" (default: entire in-scope surface)

## Status

`data/pypy_known_bugs.tsv` is seeded **empty**. Unlike `cpython-review-toolkit`'s
`cpython_known_bugs.tsv` (seeded from real fusil OOM/TSan findings) and
`rustpy-review-toolkit`'s `known_panics.tsv` (seeded from a fuzzing campaign),
this toolkit has no PyPy-specific fuzzing corpus yet. The schema exists so this
command has somewhere to grow into once fusil-on-PyPy (running in parallel,
per danzin) produces confirmed instances.

## Workflow

1. Run all four scanners.
2. Load `data/pypy_known_bugs.tsv`. If empty, say so plainly and report that
   this command currently only has a schema, not a seeded catalog — don't
   silently produce an empty "no known issues" result that reads as a clean
   bill of health.
3. For each catalog entry (once seeded): check whether the corresponding
   scanner still finds it (`present`), whether the file/line has drifted
   (`line_drifted`), whether the containing function is gone
   (`absent_in_function`), or whether it's genuinely fixed (`absent`).

## Usage

```
/pypy-review-toolkit:known-issues
```
