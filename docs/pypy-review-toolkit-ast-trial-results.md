# pypy-review-toolkit — AST vs. Tree-sitter Trial (per danzin's question)

*Ran against the full `rpython/` + `pypy/` tree in the cloned checkout:
2,548 real `.py` files. This directly tests danzin's concern — does
Python 3's `ast.parse()` recover useful signal from 2.7-only syntax — with
actual numbers instead of assumptions.*

## Result: the concern was correct, and the fallback works

**`ast.parse()` (Python 3) fails on 443 of 2,548 files — a 17.4% failure
rate.** That's much higher than the plan's architecture section implied by
treating `ast` as sufficient on its own; this is a real gap, not a
theoretical one.

**Failure breakdown, all real Python-2-only syntax, none of it exotic:**

| Cause | Count |
|---|---|
| `print` statement (no parens) | 212 |
| leading-zero octal literals (`0755`) | 50 |
| invalid decimal literal (long-int `L` suffix etc.) | 48 |
| parenthesized tuple-unpacking function params (`def f((a, b)):`) | 44 |
| `exec` statement (no parens) | 35 |
| other invalid syntax | 31 |
| invalid hex literal | 13 |
| parenthesized lambda tuple params | 6 |
| bare `except X, e:` | 2 |

**Tree-sitter-python, run on exactly those 443 files that broke `ast`:**

| Outcome | Count |
|---|---|
| Parsed with zero error nodes | 399 (90%) |
| Parsed with some error nodes (partial recovery) | 44 (10%) |
| Hard failure (raised an exception) | **0** |

Zero hard failures across all 443 — tree-sitter-python's grammar tolerates
this syntax directly in most cases, and degrades gracefully (local ERROR
nodes, not a lost file) in the rest.

**Critically, the 44 "partial recovery" files still keep almost all their
usable structure.** Sampled `rpython/rlib/jit.py` — the single file the
crown-jewel `immutability-contract-auditor` depends on most, per the design
doc's §3.2 — has a local error node somewhere but tree-sitter still
recovers all 45 top-level functions and 24 top-level classes. A scanner
walking function/class definitions loses essentially nothing on this file,
even though `ast.parse()` would refuse it outright.

## What this changes in the design

**§2.1 of the design doc is now wrong in isolation** — "the parser is
`ast`, not tree-sitter" needs to become "the parser is `ast`, with
tree-sitter-python as an explicit, load-bearing fallback for the ~17% of
files `ast` can't handle, not an edge-case afterthought." Concretely:

- `discover_pypy.py` should try `ast.parse()` first (cheap, and gives real
  Python AST nodes for the ~83% that succeed, which is worth keeping —
  tree-sitter's tree shape is different and every scanner built against
  `ast.AST` would need dual code paths otherwise), and fall back to
  tree-sitter-python only on `SyntaxError`.
- This means `pypy-internals-mapper` (or a shared parsing helper it owns)
  needs to expose a uniform interface over "this file gave me an
  `ast.Module`" vs. "this file gave me a tree-sitter tree" — scanners
  shouldn't each reimplement that dispatch.
- `tree-sitter` + `tree-sitter-python` become a hard dependency, not
  optional — 17% of the reviewable surface is unreachable without it. This
  is a different posture than `cext-review-toolkit`'s "no silent
  degradation, print an error and exit" stance on missing tree-sitter,
  since here the fallback is covering for a language-version mismatch, not
  choosing a fundamentally different parsing strategy.
- Vendoring model (§7 of the design doc) needs a note: `code-review-toolkit`
  vendors `scan_common.py`/`analyze_history.py`/`measure_complexity.py`
  which assume `ast.parse()` never fails outright on a `.py` file — for
  PyPy those need the same fallback wired in, which is a real local seam,
  not the "verbatim, no local changes" posture rustpy documents for its
  own chassis.

## On danzin's second point — a CPython oracle

Noted and worth folding into the roadmap now rather than later: the same
pattern `cpython-review-toolkit`'s parity-checker and (proposed, v0.2+)
`rustpy-review-toolkit`'s CPython differential oracle both use — running
equivalent code under two implementations and treating a divergence as a
confirmed, localized bug — applies here too, and arguably more directly,
since PyPy's entire purpose is behavioral parity with CPython. This is a
strong v0.2 candidate, gated behind having a working PyPy build to run
against (same "needs a built interpreter" caveat `cpython-review-toolkit`
gives its own dynamic checks), and worth scoping properly once v0.1's
static agents have something to validate against each other on.

## Answers to the four open questions, for the record

1. Fusil-toward-PyPy — danzin running this in parallel.
2. `translated-divergence-auditor` as flagship — kept as a working
   hypothesis, not confirmed; pivot if data suggests otherwise once real
   findings come in.
3. Repo home — agreed.
4. JIT backend codegen out of scope — agreed.
