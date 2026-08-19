---
description: "Quick health dashboard — all scanner-backed agents in summary mode"
argument-hint: "[scope]"
allowed-tools: ["Bash", "Glob", "Grep", "Read", "Task"]
---

# PyPy Review Toolkit — Health Dashboard

Run all scanner-backed agents in summary mode for a quick health snapshot.

**Scope:** "$ARGUMENTS" (default: entire in-scope surface per `discover_pypy.py`
— rlib, jit, gc, interpreter, objspace, module; annotator/rtyper/translator are
explicitly out of scope)

## Workflow

1. Run `discover_pypy.py` to confirm this is a real PyPy checkout and get the
   layer classification. If it isn't a recognized checkout, say so plainly
   rather than proceeding as if it were.
2. Run each scanner in turn: `scan_translated_divergence.py`,
   `scan_immutability_contracts.py`, `scan_interp_app_boundary.py`,
   `scan_rpython_restrictions.py`.
3. Dispatch each corresponding agent (`translated-divergence-auditor`,
   `immutability-contract-auditor`, `interp-app-boundary-checker`,
   `rpython-restriction-scanner`) in summary mode — top findings only, no deep
   per-site analysis. Run at most 2 concurrently.
4. Deduplicate before scoring.
5. Synthesize:

```markdown
# PyPy Review Toolkit — Health Dashboard

| Dimension | Status | Score | FIX/CONSIDER-high | Top Finding |
|---|---|---|---|---|
| Translated divergence (flagship, working hypothesis) | 🟢/🟡/🔴 | X/10 | N | [summary] |
| Immutability contracts (crown jewel) | 🟢/🟡/🔴 | X/10 | N | [summary] |
| Interp/app boundary | 🟢/🟡/🔴 | X/10 | N | [summary] |
| RPython restrictions (kwargs only — see scanner scope note) | 🟢/🟡/🔴 | X/10 | N | [summary] |

## Overall Health: X/10

## Scope Note

RPython-restriction scanning currently covers unbounded `**kwargs` only.
`eval`/`exec` are deliberately not checked (investigation found zero genuine
violations among 21 real candidates). Mixed-type containers and generators
crossing the translation boundary are not yet implemented.

## Top 3 Priorities
1. [Most impactful]
2. [Next]
3. [Next]

For detailed analysis, run:
  /pypy-review-toolkit:explore [scope]
```

## Scoring Rubric

Same anchors as the family convention:
- **10**: no findings above ACCEPTABLE.
- **8-9**: only CONSIDER, no FIX.
- **6-7**: a few FIX items, several CONSIDER.
- **4-5**: multiple FIX items or a systemic CONSIDER pattern.
- **1-3**: many FIX items or critical systemic issues.

🟢 8-10 | 🟡 5-7 | 🔴 1-4

## Usage

```
/pypy-review-toolkit:health
/pypy-review-toolkit:health rpython/rlib
```
