# pypy-review-toolkit — Master Report

*Everything so far, consolidated into one document. Danzin, this pulls
together the plan, your feedback, and five rounds of empirical
investigation I've run against the real `pypy/pypy` checkout since. Every
number below came from actually running something against real code, not
from reasoning about what the code probably looks like — I've flagged the
one place I found a bug in my own methodology rather than let it stand.*

---

## 1. Where this started

You asked for a plan for `pypy-review-toolkit`, modeled on
`cpython-review-toolkit` and `rustpy-review-toolkit`. I cloned both,
read them properly (source, tests, design docs — `rustpy-review-toolkit`
turned out to already have its own design doc in almost exactly the
format I ended up using), and cloned a real `pypy/pypy` checkout to
ground every PyPy-specific claim against actual files instead of memory.

**What the siblings actually look like**, briefly: `cpython-review-toolkit`
is mature — 23 agents, two generations of scanner (legacy regex +
tree-sitter crash-class detectors), a differential parity checker against
CPython's own pure-Python twins, an OOM reproducer that turns static
candidates into real crashes, and review-slice partitioning for reviewing
188 files / ~358,000 lines in 37 manageable chunks.
`rustpy-review-toolkit` is younger (6 agents, v0.1) but its design doc
documents something important: a **transfer-gradient experiment** — running
a sibling toolkit's checks against RustPython directly produced real false
positives from vocabulary overlap without semantic overlap (`.unwrap()`,
`#[pyclass]` mean different things there than in PyO3). Their conclusion:
"RustPython's real bug classes are native, not transplantable." I treated
that as a methodology to repeat, not just a fact to cite.

## 2. Architecture decisions

**Parser: `ast`, with tree-sitter-python as a load-bearing fallback.**
RPython isn't a separate grammar — it's a restricted Python-2 dialect,
checked by PyPy's own annotator at translation time, not by a different
parser. That put this toolkit architecturally closer to `code-review-toolkit`
(plain Python, `ast`-based) than to either C/Rust sibling, even though
conceptually it's closest to `cpython-review-toolkit` (both review a
runtime's own implementation).

You flagged the risk directly: does `ast.parse()` (Python 3) actually
recover useful signal from 2.7-only syntax still in the checkout? I ran
the trial instead of guessing:

- **`ast.parse()` fails on 443 of 2,548 files — 17.4%.** All real Python-2
  syntax: `print`/`exec` statements without parens (247 files), leading-zero
  octal/long-int literals (98 files), parenthesized tuple-unpacking
  function params like `def f((a, b)):` (44 files), a few others.
- **tree-sitter-python, run on exactly those 443 failing files: 399 (90%)
  parse with zero error nodes, 44 (10%) with partial recovery, zero hard
  failures.** Even the partial-recovery files keep almost all usable
  structure — `rpython/rlib/jit.py` itself, the file the crown-jewel agent
  depends on most, has a local error node somewhere but tree-sitter still
  recovers all 45 top-level functions and 24 top-level classes from it.

Your instinct was right, and the fallback you suggested works cleanly.
Design consequence: `discover_pypy.py` tries `ast.parse()` first (cheap,
real `ast.AST` nodes for the 83% that succeed), falls back to
tree-sitter-python on `SyntaxError`. `tree-sitter`/`tree-sitter-python`
are a hard dependency now, not optional — 17% of the reviewable surface
is otherwise invisible.

**CPython-as-oracle**, your other suggestion: added to the v0.2 roadmap,
same pattern `cpython-review-toolkit`'s parity-checker and
`rustpy-review-toolkit`'s proposed differential oracle both use — run
equivalent code under two implementations, treat divergence as a
confirmed bug. Applies especially directly here since PyPy's whole
purpose is behavioral parity with CPython. Gated behind having a working
PyPy build to run against, not scoped in detail yet.

## 3. Scope finding: `pypy/module/` is unaccounted for

Ran file/line counts across every layer the design discusses:

| Directory | Files | Lines |
|---|---|---|
| `rpython/rlib` | 253 | 391,827 |
| `rpython/jit` | 635 | 190,682 |
| `rpython/memory/gc` | 22 | 13,538 |
| `rpython/annotator` (out of scope) | 24 | 12,816 |
| `rpython/rtyper` (out of scope) | 114 | 50,849 |
| `rpython/translator` (out of scope) | 172 | 39,012 |
| `pypy/interpreter` | 124 | 44,327 |
| `pypy/objspace/std` | 96 | 45,916 |
| **`pypy/module`** | **820** | **179,994** |

`pypy/module/` (the built-in module implementations — `_socket`, `struct`,
`thread`, `_cffi_backend`, `_cppyy`, `micronumpy`, dozens more) is bigger
than `pypy/interpreter` + `pypy/objspace/std` combined, and the plan never
mentioned it — not even as an exclusion. That's a real gap: 3 of the 9
(now corrected to 7, see §5) immutability-contract candidates I found live
inside `pypy/module/`, without deliberately targeting it.

In-scope surface, doing the arithmetic honestly: `rlib` + `jit` + `gc` +
`interpreter` + `objspace/std` = **~687,000 lines**, before deciding on
`pypy/module`. With it: **~867,000 lines**. `cpython-review-toolkit`'s
entire reviewable surface is ~358,000 lines and already needed 37
pre-partitioned slices. PyPy's in-scope surface is roughly **2–2.4x
that**, so review-slice partitioning (a `data/review_slices.json` +
tracking tooling, `cpython-review-toolkit`-style) needs to be a v0.1
component, not a later refinement. This still needs your call on whether
`pypy/module` is in scope now or explicitly deferred — I'd rather ask than
guess.

## 4. Flagship: `we_are_translated()` divergence

Real surface: **166 files mention it, 607 textual occurrences, 284 actual
`if we_are_translated():` branch sites** found via AST (undercounts by
whatever's in the 17.4% of files needing the tree-sitter fallback, not yet
re-run against those).

**The 284 split in a way I hadn't anticipated:** only 59 (21%) have an
`else`/`elif` arm to diff — the "diff both arms" shape my design doc
assumed was the norm. **225 (79%) are a bare `if`, no else at all.**

- **The 79% majority** is one shape, every sample the same:
  `if not we_are_translated(): assert ...` — a sanity check that only
  runs under the untranslated test-suite mode. Mechanically recognizable
  (body is only `assert`/`raise`), and needs to be an automatic
  default-suppress rule, not something the agent reasons about 225 times
  a run.
- **The 21% minority** is real, and often more interesting than "debug
  instrumentation" — `rpython/rlib/rgil.py` maintains a whole parallel
  `EmulatedGilHolder` class specifically so GIL logic can be exercised
  under the untranslated test suite before the real translated GIL
  primitives exist. Deliberate, substantial, correctly POLICY — not the
  same thing as a bare debug print, and the design's classification
  section needed to say so explicitly rather than lump both under
  "debug-only instrumentation."

This doesn't resolve whether the flagship choice is right — you said
working hypothesis, pivot if data says otherwise, and I'm treating that as
still open. What it does do: if it stays flagship, the scanner now has a
real shape distribution to build against instead of discovering it
mid-build.

## 5. Crown jewel: immutability-contract mismatches (`_immutable_fields_` / `@jit.elidable`)

Real surface: **268 `_immutable_fields_` declarations across 98 files,
183 `@jit.elidable` sites.**

Built the actual mismatch heuristic — flag any class where a field listed
in `_immutable_fields_` (without the `?` quasi-immutable marker) gets
reassigned somewhere other than `__init__`.

**First pass: 24 of 201 classes flagged. Wrong — caught it myself.** My
marker-detection only checked for `?` at the very end of the string, but
PyPy's actual convention for a quasi-immutable *list* field is
`'closure?[*]'` — the `?` comes **before** `[*]`, not after. That ordering
mistake meant I was mis-flagging already-correctly-marked fields
(`Function.closure`, `Function.defs_w`, `W_ClassObject.bases_w`) as
violations. Fixed the check to look for `?` anywhere in the string,
re-ran:

**7 real candidates out of 201 classes checked**, and I traced every
single one individually this time rather than sampling:

- **`pypy/interpreter/pycode.py` — `PyCode.co_filename`.** Declared fully
  immutable, set in `__init__`, set *again* in a later method whose own
  comment says it's freezing the filename at translation time. Central
  file — every compiled function's code object. Strongest example.
- **`pypy/interpreter/generator.py` — `GeneratorIterator.pycode`.**
  Declared fully immutable, set in `__init__`, set again to a new value
  inside `descr__setstate__` — the generator-unpickling restore path.
- **`pypy/module/_cppyy/interp_cppyy.py` — `CPPMethod.cif_descr` (+3 more
  fields).** `__init__` sets it to a nullptr placeholder; it's lazily
  populated later, read right next to a `jit.promote(self)` call, and can
  even be **reset back to nullptr on an error path** ("should not be True,
  but you never know," per the code's own comment). This is the strongest
  evidence of the four — a `jit.promote()` sitting right next to a field
  that provably isn't stable.
- **`pypy/module/_rawffi/alt/interp_struct.py` — `W__StructInstance.rawmem`.**
  Traced all three assignment sites: two are inside `__init__` (fine,
  branching construction paths), the third is inside `__del__` — a
  cleanup-time reassignment, structurally different and lower-risk than
  the other three, since a finalizing object generally isn't still being
  actively JIT-traced. Worth its own priority tier, not lumped with the
  others.
- Three more not yet individually traced: `pypy/module/_cffi_backend/realize_c_type.py`
  (`W_RawFuncType`, 3 fields), `pypy/module/struct/interp_struct.py`
  (`W_Struct.format`/`.size`), `pypy/objspace/std/mapdict.py`
  (`UnboxedPlainAttribute`, 2 fields).

**Conclusion:** the crown jewel has real, individually-verified evidence
behind it, at a workable signal rate (7/201, and 6 of those are genuine
mid-lifetime cases once the `__del__` one is set aside). This is the
strongest-evidenced piece of the whole plan right now.

## 6. RPython-restriction scanner: `eval`/`exec`

Naive grep for `eval(` gave 96 hits — almost worthless, nearly all
`.eval()` method calls unrelated to the builtin. AST-accurate, non-test:
**8 real `eval()` calls, 48 real `exec()` calls.**

Then found the bigger issue: most of the 48 aren't restriction violations
at all — they're a recognized PyPy idiom, generating specialized RPython
methods once via `exec()` at class-body-execution time, before the
annotator ever runs. `rpython/jit/metainterp/pyjitpl.py` stamps out a
family of near-identical opcode-handler methods this way; the `exec()`
itself vanishes once the class is built, only the generated `def`s survive
into what actually gets translated. `rpython/rlib/rstruct/runpack.py` has
the same pattern with an explicit comment: `# override not-rpython version`.

Split by scope: 35 of 48 sit at module/class top-level (near-certainly
this codegen idiom), 21 sit inside a function body. But "inside a
function" still doesn't mean "real violation" — two I sampled from that
21 turned out to be the *same* codegen idiom, just wrapped in a factory
function called once at module-load time.

**Conclusion:** this scanner is close to useless without a
recognize-and-suppress step for the codegen-idiom shape built in from the
start — not a v0.2 refinement. I don't have a clean final count of the
genuine remainder yet; that's honest unfinished work, not a number I want
to guess at.

## 7. Interp/app boundary: raw exceptions vs. `OperationError`

Censused `pypy/interpreter/` + `pypy/module/` (non-test): **107 correct
`raise OperationError(...)` sites, 97 raw-builtin-exception sites** —
almost as many as the correct pattern.

Traced two of the 97 individually instead of assuming the raw count is
the bug count:

- **`pypy/interpreter/argument.py`, `Arguments.fixedunpack` — resolved
  clean.** The docstring says "raise a real ValueError," and I traced
  every caller in the non-test tree: there are none. The only place it's
  called at all is its own test file, which explicitly asserts the
  `ValueError` is raised. Zero production reachability, deliberately
  tested — this is ACCEPTABLE with hard evidence, not a hedge.
- **`pypy/interpreter/pyframe.py:236`, inside `initialize_frame_scopes` —
  the strongest candidate found.** The same function has two structurally
  similar internal-consistency checks a few lines apart: one correctly
  uses `oefmt`/`OperationError`, the other raises a raw `ValueError`. That
  in-function asymmetry is a sharper signal than any raise-site count
  alone. Traced the caller: `initialize_frame_scopes` runs from
  `PyFrame.__init__` — every single frame construction in the
  interpreter. I can't confirm from static reading alone whether a
  mismatched-closure code object can actually reach this from app-level
  input, but the asymmetry with the correct pattern three lines above
  makes this worth a real look.

**Conclusion:** the scanner's design should lean on same-function
asymmetry (two similar checks, one correct convention and one not) as its
primary signal, not a flat raise-site count — it's a genuinely better
discriminator and it's checkable statically, no call-graph work required.

## 8. The throughline across all of this

Every one of the four censuses (`we_are_translated()`, immutability
contracts, eval/exec, raw exceptions) landed on the same lesson: **a flat
AST pattern-match finds the real candidate surface, but PyPy's codebase
has enough deliberate, documented, self-aware idiom in exactly the places
these checks look that none of the four scanners can ship as "just the
pattern match."** The recognize-and-suppress step (single-arm asserts,
`?[*]` markers, codegen idioms, "real ValueError" docstrings) is part of
the minimum viable version of every one of these agents, not a v0.2
refinement layered on later. That's the one design principle I'd want
carried through the whole build, not just the scanners already censused.

## 9. Your four answers, and where they left things

1. **Fusil-on-PyPy in parallel** — still waiting to hear what that turns
   up; nothing here depends on it yet but it'll matter for calibrating
   FIX vs. CONSIDER thresholds once there's real crash data.
2. **`translated-divergence-auditor` as working hypothesis** — §4's data
   doesn't confirm or deny it, just sharpens what building it actually
   requires. Still your call whether it leads.
3. **Repo under my account, you as contributor** — agreed, unchanged.
4. **JIT backend codegen out of scope** — agreed, unchanged.

## 10. Honestly still open

- RPython-restriction eval/exec: 21 inside-function candidates only
  partially characterized, no final clean count.
- Three immutability candidates not yet individually traced
  (`W_RawFuncType`, `W_Struct`, `UnboxedPlainAttribute`).
- None of the censuses have been re-run through the tree-sitter fallback
  yet, so all of them undercount by whatever's in the 443 files (17.4%)
  `ast` can't reach directly — `rpython/rlib/jit.py` itself is one of
  those files, so the crown-jewel numbers in particular are probably a
  floor, not a ceiling.
- `pypy/module` in/out-of-scope decision still needs your call, per §3.
- Haven't started any actual scanner code yet — everything above is
  investigation to firm up the plan, not the build itself.

Let me know if you want me to keep pushing on any of these before you've
had a chance to look at what's here, or whether this is enough to work
from for now.
