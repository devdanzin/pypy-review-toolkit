---
description: "Where to look first — flagship + crown-jewel findings, ranked"
argument-hint: "[scope]"
allowed-tools: ["Bash", "Glob", "Grep", "Read", "Task"]
---

# PyPy Review Toolkit — Hotspots

Surface the highest-signal findings first: `translated-divergence-auditor` and
`immutability-contract-auditor` — the flagship and crown jewel, the two agents
with real, individually-traced evidence behind them (see the design doc's
investigation trail).

**Scope:** "$ARGUMENTS" (default: entire in-scope surface)

## Workflow

1. Run `scan_translated_divergence.py` and `scan_immutability_contracts.py`.
2. Dispatch `translated-divergence-auditor` and `immutability-contract-auditor`
   for real review of every FIX and CONSIDER/high finding.
3. Rank output:
   - `immutability-contract-mismatch-promoted` findings first (strongest
     evidence tier — a live object read by JIT-promoted code)
   - `we-are-translated-two-arm-divergence` findings classified FIX
   - Remaining CONSIDER findings from both agents, ordered by file churn if
     `analyze_history.py` data is available (a high-churn file with a
     CONSIDER finding is a stronger priority than a stable one)
4. Present as a ranked list, not a table — hotspots are about "look here
   first," not a dashboard.

## Usage

```
/pypy-review-toolkit:hotspots
/pypy-review-toolkit:hotspots pypy/module/_cppyy
```
