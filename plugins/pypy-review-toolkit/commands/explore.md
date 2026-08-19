---
description: "Full exploration — discovery, history context, all agents, synthesis"
argument-hint: "[scope] [aspect] [depth]"
allowed-tools: ["Bash", "Glob", "Grep", "Read", "Task"]
---

# PyPy Review Toolkit — Explore

Full review pipeline, phased so later agents can use earlier agents' output as
context — same shape as `cpython-review-toolkit`'s phase table.

**Scope:** "$ARGUMENTS" (default: entire in-scope surface — rlib, jit, gc,
interpreter, objspace, module; annotator/rtyper/translator explicitly excluded)

## Phases

### Phase 1 — Discovery
Run `discover_pypy.py --slices`. Confirm this is a recognized PyPy checkout
before proceeding. If the in-scope surface is large enough to need review-slice
partitioning (real numbers: ~687K-867K lines in scope, roughly 2-2.4x
`cpython-review-toolkit`'s ~358K-line surface, which needed 37 slices at that
smaller size), work through `review_slices.json` slice by slice rather than
attempting the whole tree in one pass.

### Phase 2 — History Context
Run `analyze_history.py` for the scope. This context feeds `git-history-analyzer`
in the final phase and gives every other agent temporal grounding.

### Phase 3 — Flagship + Crown Jewel
Run `scan_translated_divergence.py` and `scan_immutability_contracts.py`.
Dispatch `translated-divergence-auditor` and `immutability-contract-auditor`.
These are the two agents with real, individually-traced evidence behind every
finding class — prioritize their output.

### Phase 4 — Boundary + Restriction
Run `scan_interp_app_boundary.py` and `scan_rpython_restrictions.py`. Dispatch
`interp-app-boundary-checker` and `rpython-restriction-scanner`. Remember the
restriction scanner's narrow scope (kwargs only) when reporting — don't imply
broader coverage than exists.

### Phase 5 — Qualitative
Dispatch `jit-trace-reviewer` (no backing script — judgment only, and should be
reported with appropriately lower confidence than the scanner-backed phases).

### Phase 6 — History-Last Synthesis
Dispatch `git-history-analyzer` last, with all prior findings available, for
fix-completeness review, similar-bug detection, and churn×risk prioritization.

### Phase 7 — Synthesis
Combine into a single report: findings grouped by classification (FIX first),
then by agent. Note the toolkit's current honesty gaps explicitly rather than
letting silence imply completeness:
- `translated-divergence-auditor` is the flagship as a **working hypothesis**,
  not confirmed — this may pivot.
- No PyPy-specific known-bugs catalog exists yet (see `/known-issues`).
- The restriction scanner covers unbounded `**kwargs` only.

## Usage

```
/pypy-review-toolkit:explore
/pypy-review-toolkit:explore pypy/module/_cppyy
/pypy-review-toolkit:explore rpython/rlib deep
```
