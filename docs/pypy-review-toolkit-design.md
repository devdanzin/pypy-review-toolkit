# pypy-review-toolkit — Master Plan (design document v2.0)

**Status: v0.1 built and validated (4 scanners, 6 agents, 4 commands, 35
tests, all against the real checkout); now updated with the first real,
fuzzer-confirmed bug data.** danzin ran fusil against PyPy overnight and
found 5 real bugs (`PYPY_FINDINGS_REPORT.md`). This is the single most
important input this plan has received since danzin's original review —
it's the first evidence that isn't reasoning from reading source, and it
changes the priority ranking (§9). Full investigation trail:
`pypy-review-toolkit-ast-trial-results.md`,
`pypy-review-toolkit-we-are-translated-census.md`,
`pypy-review-toolkit-crown-jewel-and-scope-findings.md`,
`pypy-review-toolkit-round4-restrictions-and-boundary.md`,
`pypy-review-toolkit-round5-resolved-findings.md`,
`pypy-review-toolkit-MASTER-REPORT.md`, and now
`PYPY_FINDINGS_REPORT.md` (danzin's fuzzing report). This document is the
one to build from; those are the evidence behind it.

**The one principle carried through every section below:** every bug class
investigated turned out to have a real candidate surface *and* a real
false-positive source from deliberate, documented PyPy idiom sitting
exactly where the naive check looks. Every agent's minimum viable version
includes its recognize-and-suppress step — that's not a v0.2 refinement,
it's part of v0.1, because every census so far needed it before the
numbers meant anything.

## 0. What the fuzzer found, and why it matters more than everything else in this doc

Read `PYPY_FINDINGS_REPORT.md` in full before anything else here — this
section summarizes it, but the report itself has the reproducers and the
"Notes for a static-analysis toolkit" section danzin wrote specifically
for this project.

**Five real bugs, three abort/crash the interpreter, two leak an internal
`SystemError`:**

| # | Bug | Shape | Severity |
|---|---|---|---|
| 1 | `compile(b"0\x80", ...)` → SIGABRT | PEG-parser assertion failure | Not statically checkable (C-generated parser code) |
| 2 | `struct.unpack("u", b"abcd")` → `SystemError` | unvalidated value into an RPython helper (`unichr_as_utf8`) | **Shape A** |
| 3 | `codecs.getincrementalencoder("cp949")().setstate(-1)` → `SystemError` | unvalidated value into an RPython helper (`rbigint.tobytes`) | **Shape A** |
| 4 | `_lzma` double free | `ffi.NONE` typo (doesn't exist) after a free, plus an unlocked check-then-free | **Shape B** + **Shape C** |
| 5 | `TextIOWrapper._dealloc_warn` on a detached wrapper → SIGSEGV | incomplete fix: guard added to one layer of a wrapper stack, not the sibling layer | **Shape D** |

**The single most important thing about this data**: none of the five
bugs match `we_are_translated()` divergence or an immutability-contract
mismatch — the two bug classes this plan spent five investigation rounds
validating and built scanners for first. That's not a reason to abandon
either (§9 explains why), but it's exactly the kind of evidence danzin
asked for when he said "working hypothesis, pivot if data suggests we
should" — and this is real data, the first this plan has had that isn't
static reasoning about source code.

### 0.1 Bug shapes, mapped to what this toolkit should check for

Danzin's own report includes a "Notes for a static-analysis toolkit"
table — reproduced and extended here with what's actually checkable given
what this toolkit already has:

**Shape A — user value flows into an RPython helper without that
helper's precondition being checked first.** Both confirmed instances
(`unichr_as_utf8` needs a codepoint `<= U+10FFFF`, non-surrogate;
`rbigint.tobytes` needs a non-negative value) are calls from
`pypy/module/*` into `rpython.rlib` helpers, where the argument traces
back to an app-level value via `unwrap_spec`, with no range/sign check in
between. Confirmed real, both `unichr_as_utf8` and `rbigint.tobytes`
verified to exist exactly as named in the checkout (`rpython/rlib/rutf8.py:40`,
`rpython/rlib/rbigint.py:392`). **New v0.1-priority scanner, see §9.1.**
Danzin's report also gives a strong dynamic oracle for free: grep any
candidate program's stderr for `"unexpected internal exception (please
report a bug)"` — PyPy is telling you it hit exactly this bug class.

**Shape B — a raw `free()` call in hand-written (not RPython-translated)
Python-over-cffi stdlib code**, specifically under `lib_pypy/`. **This is
a real scope gap this plan never accounted for.** Checked the real
checkout: `lib_pypy/` exists, and 2 of danzin's named 3 files are present
and do call `free()` — `lib_pypy/gdbm.py` and `lib_pypy/_pypy_util_cffi.py`
(`lib.free(ffi.cast("void*", self._p))`). `lib_pypy/_lzma.py` is **not**
present in the `main` branch as cloned — see §0.2, this is a checkout/
version mismatch, not evidence the bug doesn't exist. `lib_pypy/` needs to
be added to `discover_pypy.py`'s in-scope layers.

**Shape C — assignment to a non-existent attribute on an object, on an
error path.** `ffi.NONE` doesn't exist (should be `ffi.NULL`) — invisible
under normal testing because it only executes after a free, on a path
that's rarely exercised. Checkable narrowly: flag `ffi.<name>` attribute
accesses where `<name>` isn't in a known-good set (`NULL`, `new`, `cast`,
`sizeof`, `string`, `buffer`, `gc`, `typeof`, `alignof`, `offsetof`,
`getctype`, `addressof`, `from_buffer`) — a coarse allowlist, not a real
resolution of cffi's actual API, but cheap and exactly targeted at this
bug's shape.

**Shape D — one method missing a guard that every sibling method on the
same class has.** `_dealloc_warn_w` dereferences `w_buffer` without the
`_check_attached`-style guard every other method on the class uses before
touching the same field. **This generalizes the same-function-asymmetry
idea `interp-app-boundary-checker` already uses, one level up: instead of
"two checks in the same function, one correct convention and one not,"
it's "N methods on the same class, N-1 guard a field before use, one
doesn't."** Real, checkable, and the report's own table proposes almost
exactly this check. **New v0.1-priority scanner, see §9.2.**

**Shape E — a fix applied to one layer of a wrapper stack, not checked
against the analogous field on a sibling layer.** Same underlying pattern
as Shape D, but temporal — git-history-analyzer's similar-bug-detection
capability (§4.1) is the natural home for this: when a fix commit adds a
guard to one class, search sibling classes in the same subsystem for the
same field-access pattern without the equivalent guard. `interp_bufferedio.py`'s
`w_raw` guard vs. `interp_textio.py`'s ungued `w_buffer` is exactly the
`pypy/module/_io/` wrapper-stack shape this should generalize from.

**Namespace leakage** (why Shapes B/C/D are reachable at all — PyPy
exposes 14 names on `LZMADecompressor` where CPython exposes 5) is real
and important context but not itself a new scanner: it needs a real
CPython source comparison to check properly, which this toolkit doesn't
have infrastructure for yet. Deferred to v0.2, noted honestly rather than
attempted with an unreliable heuristic.

### 0.2 A real methodology gap this surfaced: checkout version pinning

Trying to verify bug #4 and #5 against the real checkout surfaced
something this plan hadn't accounted for at all: **`lib_pypy/_lzma.py`
doesn't exist in the `main` branch as cloned**, and `_dealloc_warn_w`
(the exact method name, confirmed via `pypy/doc/release-v7.3.18.rst`'s
release notes, which is `_dealloc_warn_w` not `_dealloc_warn` — PyPy's
`_w` suffix convention for `interp2app`-wrapped methods) doesn't appear
anywhere in `pypy/module/_io/interp_bufferedio.py` or `interp_textio.py`
in this checkout either. Danzin's report targets **PyPy 7.3.23 /
Python 3.11.15** specifically; every census and scanner validation run so
far in this plan has been against whatever `main` HEAD happened to be
(dated 2026-08-16 at last clone) — which may be tracking a different,
likely newer/in-development Python version where these modules have
moved, been renamed, or don't exist yet in the same form.

**This matters beyond just these two bugs.** Every real number in §§1-8 of
this document (the `we_are_translated()` census, the immutability-contract
count, all of it) was run against that same `main` checkout. If `main`
doesn't match the release under active development/fuzzing, some of those
numbers could already be stale relative to whatever branch actually
matters for real bug-hunting. **This toolkit needs an explicit,
documented step: pin the checkout to the specific branch/tag matching
what's being reviewed or fuzzed, and re-verify (at least spot-check) prior
findings against that branch before trusting them as current.** Concretely,
`discover_pypy.py`'s output should include the checked-out branch/commit
and, where checkable, the PyPy version string, so a report can say what it
was actually run against rather than leaving that implicit.



## 1. Project identity

`pypy-review-toolkit` is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
plugin that statically reviews **PyPy's own implementation** — the RPython
interpreter core, object space, and JIT — for correctness bugs and translation
hazards. It reviews the runtime itself, the way `cpython-review-toolkit`
reviews CPython's C runtime and `rustpy-review-toolkit` reviews RustPython's
Rust runtime. It is not for reviewing code that *runs on* PyPy, and not for
the CPython-compatible stdlib PyPy ships in `lib-python/`/`lib_pypy/`.

### Relationship to the sibling toolkits

| Toolkit | Reviews | Base language | Parser |
|---|---|---|---|
| [code-review-toolkit](https://github.com/devdanzin/code-review-toolkit) | Python source (general) | Python | `ast` |
| [cpython-review-toolkit](https://github.com/devdanzin/cpython-review-toolkit) | CPython runtime | C | regex (legacy) + tree-sitter (crash-class) |
| [cext-review-toolkit](https://github.com/devdanzin/cext-review-toolkit) | C extensions (consumer side) | C | tree-sitter |
| [rust-ext-review-toolkit](https://github.com/devdanzin/rust-ext-review-toolkit) | Rust/PyO3 extensions (consumer side) | Rust | tree-sitter |
| [pyo3-review-toolkit](https://github.com/devdanzin/pyo3-review-toolkit) | PyO3 framework (implementer side) | Rust | tree-sitter |
| [rustpy-review-toolkit](https://github.com/devdanzin/rustpy-review-toolkit) | RustPython runtime | Rust | tree-sitter |
| **pypy-review-toolkit** | **PyPy runtime (this project)** | **RPython (a restricted Python-2 dialect)** | **`ast`** |

**Conceptually** the closest relative is `cpython-review-toolkit` — both
review the implementation of a Python runtime, from the implementer's
perspective, for the same broad question ("where are the bugs, style
violations, and translation/portability risks in this codebase?"). **But
architecturally**, the closer relative is `code-review-toolkit`: RPython is
not a separate grammar the way C or Rust is — it's Python source, restricted
and statically typed by PyPy's own annotator at translation time, and every
`.py` file under `rpython/` and `pypy/` parses cleanly with the standard
library `ast` module. There is nothing here that needs tree-sitter. This
mirrors how `rustpy-review-toolkit` didn't simply reuse
`cpython-review-toolkit`'s C-oriented tooling just because both review "a
Python runtime" — it built on the tooling that matches the *actual
implementation substrate* (Rust, hence tree-sitter-rust), not the tooling
that matches the *conceptual* sibling. Here that substrate is Python itself.

### Why a native toolkit is needed, not a transplant

`rustpy-review-toolkit`'s design doc documents a concrete experiment worth
repeating in spirit: running the PyO3-consumer toolkit's checks against
RustPython produced real matches but also **name-collision false
positives** — RustPython uses `#[pyclass]`/`.downcast()` under the same
names as PyO3 but with different semantics, and the transplanted checks
mis-fired because the check logic itself encoded PyO3's semantics, not
RustPython's.

The same risk applies here, and it's checkable without a fuzzing campaign —
just by reading what `code-review-toolkit`'s checks actually look for
(`plugins/code-review-toolkit/scripts/scan_python_pitfalls.py`, 35 checks in
its `_CHECKS` registry). Sampling that registry against what RPython
actually contains:

| code-review-toolkit check | Transfers to RPython? |
|---|---|
| `late-binding-closure-in-loop` | **Yes, cleanly.** Python closure semantics are identical in RPython source; this is a real, transplantable bug class. |
| `mutable-default-argument` | **Mostly, but needs recalibration.** RPython's annotator has its own opinions about mutable defaults that may make some instances non-issues (unreachable once the annotator would reject them) or under-report the RPython-specific version of the same hazard (a mutable *class* attribute shared across instances, which is the more common RPython footgun — see `_immutable_fields_` in §3). |
| `asyncio-fire-and-forget-task`, `blocking-call-in-async-function`, `unawaited-coroutine` | **No.** RPython has no `async`/`await` support; translation would reject it. These checks should return empty on RPython input — harmless, but worth confirming empirically in Phase 1 rather than assuming, since a check that's supposed to be silent and isn't is itself a bug. |
| `lru-cache-on-method`, `mock-callable-as-spec` | **No.** `functools.lru_cache` and `unittest.mock` are not RPython-safe usage inside `rpython/`/`pypy/interpreter`/`pypy/objspace` (though they may appear in test helper code, which is a different review surface — see §5). |
| `class-level-mutable-attribute` | **Recalibrate, don't transplant as-is.** This is close to the single most important RPython-specific pattern (§3.2, `_immutable_fields_` and quasi-immutable fields), but the *meaning* of a class-level mutable attribute in RPython is governed by the annotator/JIT's own rules, not general Python's — the check needs RPython-aware severity, not the general-Python one. |

**Conclusion:** wholesale reuse of `code-review-toolkit`'s pitfall registry
is not safe to assume works, even though the parser is identical. The
checks that transfer cleanly (closure/control-flow shape bugs) should be
vendored; the checks that need semantic recalibration
(`class-level-mutable-attribute` → the RPython immutability contract) should
be rewritten as RPython-native checks, not tuned copies; and the checks that
don't apply at all (`asyncio-*`, `mock-*`) should be confirmed empirically
to be silent-and-harmless on real RPython source in Phase 1, the same
discipline `rustpy-review-toolkit` applied before trusting any transplanted
check.

## 2. Research inputs

Unlike `rustpy-review-toolkit`, which had two prior research documents (a
fuzzing defect report and a static cross-application experiment) to
reconcile, this project currently has **one** input: direct reading of the
`pypy/pypy` checkout (see §3) plus the transfer-risk analysis in §1. There is
no PyPy-specific fuzzing corpus equivalent to `rustpy-review-toolkit`'s
fuzzer-confirmed `known_panics.tsv`, and no static cross-application
experiment has been run yet.

**This is a real gap, not a stylistic omission.** Both sibling toolkits that
have a mature classification system (`cpython-review-toolkit`'s
`cpython_known_bugs.tsv`, seeded from real fusil OOM/TSan findings plus the
CPython tracker; `rustpy-review-toolkit`'s `known_panics.tsv`, seeded from a
fuzzing campaign) earned their FIX/CONSIDER confidence from confirmed real
bugs, not from first-principles reasoning about the object model alone.
Fusil's own README says its actively-developed path targets **CPython**
specifically (`fusil-python-threaded`), not PyPy — so there is no existing
fuzzing corpus to seed a `pypy_known_bugs.tsv` from on day one. Two honest
options, not mutually exclusive:

1. Ship v0.1 as static-only, explicitly unvalidated against real PyPy bugs
   (the same posture `rustpy-review-toolkit` gave its own GC-Traverse
   checker: "real surface, 0 fuzzer-confirmed instances," capped at
   CONSIDER, never FIX, until validated).
2. In parallel, ask danzin whether extending fusil toward PyPy (per the
   earlier chat: PyPy devs said they'd accept "a clear report for relevant
   crashes with simple reproducers," which is a different bar than asking
   for large-scale fuzzing infrastructure investment) is worth doing before
   or alongside this toolkit — a small number of confirmed PyPy crashes
   would do for this toolkit what the fuzzing report did for
   `rustpy-review-toolkit`: tell us which of the bug classes below are
   real and which are theoretical.

## 3. PyPy/RPython architecture primer

Grounded against the `pypy/pypy` GitHub mirror, `main`, cloned locally
(verify against the actual reviewed commit before citing in a real report —
the same drift caveat `rustpy-review-toolkit` gives its own primer).

### 3.1 RPython is restricted Python, checked by the annotator — parsed with `ast`, falling back to tree-sitter-python

`rpython/annotator/model.py` implements abstract-interpretation-style typing
(`SomeInteger`, `SomeString`, ...) over ordinary `.py` source. There is no
separate RPython grammar. The restrictions (no `eval`, no unbounded
polymorphic containers, no arbitrary multiple inheritance, etc.) are
enforced at *translation* time by the annotator/rtyper, not at parse time
— which is exactly why a fast static approximation is useful: running the
real translator to find these is minutes, not seconds, per attempt.

**Parsing correction from v0.2, per danzin's review.** RPython source
targets Python 2.7 syntax, and Python 3's `ast.parse()` does not accept
every construct still present in the checkout. A trial run against the
full `rpython/`+`pypy/` tree (2,548 files) confirmed this is real:
**`ast.parse()` fails on 443 files (17.4%)** — `print`/`exec` statements
without parens (247 files), leading-zero octal/long-int literals (98
files), parenthesized tuple-unpacking function parameters (44 files, e.g.
`def f((a, b)):`), and a handful of other 2.x-only shapes. Running
tree-sitter-python (danzin's suggested fallback) against exactly those 443
files: **399 (90%) parsed with zero error nodes, 44 (10%) with partial
ERROR-node recovery, and zero hard failures.** Even the partial-recovery
files keep nearly all usable structure — `rpython/rlib/jit.py` itself, the
file §3.2's crown-jewel agent depends on most, has a local error node
somewhere but tree-sitter still recovers all 45 top-level functions and 24
top-level classes.

**Resulting design:** the parser is `ast.parse()` first — cheap, and gives
real `ast.AST` nodes for the ~83% of files that parse cleanly, worth
keeping rather than routing every file through tree-sitter by default — 
falling back to tree-sitter-python on `SyntaxError`. `tree-sitter` +
`tree-sitter-python` are therefore a **hard dependency**, not optional:
17% of the reviewable surface is unreachable without the fallback. This
differs from `cext-review-toolkit`'s "print an error and exit, no silent
degradation" stance on missing tree-sitter — there, missing tree-sitter
means a fundamentally worse parsing strategy; here it means a fixed
fraction of real RPython files are simply invisible without it. The
scanners need a uniform interface over "this file gave me an `ast.Module`"
vs. "this file gave me a tree-sitter tree" — `pypy_utils.py`'s job (§4.1),
not something each scanner reimplements. Full trial data in the companion
file `pypy-review-toolkit-ast-trial-results.md`.

### 3.2 JIT hints (`rpython/rlib/jit.py`)

Confirmed decorator set in the actual file: `elidable`, `unroll_safe`,
`dont_look_inside`, `promote`, `promote_string`, `promote_unicode`,
`loop_invariant`, `elidable_promote`, `purefunction`, `purefunction_promote`,
`isvirtual`, `loop_unrolling_heuristic`, `conditional_call_elidable`.

`@jit.elidable` is a purity contract (no observable side effects, result
determined by arguments alone) the JIT relies on to cache/reorder calls.
`@jit.unroll_safe` needs a JIT-compile-time-bounded loop or risks trace
explosion. `_immutable_fields_` is a class attribute the annotator/JIT give
special meaning to; real usage sampled directly from
`pypy/objspace/std/complexobject.py` (`_immutable_fields_ = ['realval',
'imagval']`) and `pypy/objspace/std/dictmultiobject.py`
(`_immutable_fields_ = ["mstrategy?"]`, trailing `?` marking a
*quasi*-immutable field — assigned at most once after construction, not
truly const). This immutability-contract-vs-actual-mutation-pattern
mismatch is the RPython-native version of the general-Python
`class-level-mutable-attribute` check flagged as needing recalibration in
§1, and is the strongest single candidate for a flagship agent (see §4.1).

**Census, run against the real checkout:** 268 `_immutable_fields_`
declarations across 98 files, 183 `@jit.elidable` sites. Built the actual
mismatch heuristic (flag a field with no `?` marker that's reassigned
outside `__init__`) and ran it — first pass over-counted (24 of 201
classes), because PyPy's quasi-immutable-list convention is
`'closure?[*]'` — the `?` comes *before* `[*]`, not after — and my first
marker check only looked at the string's tail. Corrected: **7 real
candidates out of 201 classes**, and every one was individually traced,
not sampled:

- `pypy/interpreter/pycode.py`, `PyCode.co_filename` — declared fully
  immutable, set in `__init__`, set again in a later method that
  explicitly freezes the filename at translation time. Central file —
  every compiled function's code object.
- `pypy/interpreter/generator.py`, `GeneratorIterator.pycode` — declared
  fully immutable, reassigned inside `descr__setstate__`, the
  generator-unpickling restore path.
- `pypy/module/_cppyy/interp_cppyy.py`, `CPPMethod.cif_descr` (+3 more
  fields) — `__init__` sets a nullptr placeholder; the real value is
  lazily built later, read right next to a `jit.promote(self)` call, and
  can even be reset back to nullptr on an error path ("should not be True,
  but you never know," per the code's own comment). Strongest evidence of
  the four — a `jit.promote()` sitting next to a field that's provably not
  stable.
- `pypy/module/_rawffi/alt/interp_struct.py`, `W__StructInstance.rawmem` —
  the one exception in shape: its second assignment is inside `__del__`
  (a cleanup-time reassignment), not a mid-lifetime one on a still-live
  object. Lower priority than the other three, and the agent's severity
  should distinguish this case explicitly.
- Three more identified, not yet individually traced:
  `pypy/module/_cffi_backend/realize_c_type.py` (`W_RawFuncType`, 3
  fields), `pypy/module/struct/interp_struct.py`
  (`W_Struct.format`/`.size`), `pypy/objspace/std/mapdict.py`
  (`UnboxedPlainAttribute`, 2 fields).

Full trace detail: `pypy-review-toolkit-crown-jewel-and-scope-findings.md`
and `pypy-review-toolkit-round5-resolved-findings.md`.

### 3.3 `we_are_translated()` — the PyPy-unique bug class

`rpython/rlib/objectmodel.py` defines `we_are_translated()` /
`we_are_translated_to_c()`. Code branching on this predicate runs one arm
under the untranslated (interpreted-under-CPython, used for the fast test
suite) execution mode and the *other* arm only once translated to C — the
two arms are, by construction, never exercised by the same test run. This
has no equivalent in any sibling toolkit's bug taxonomy: CPython has no
analogous dual-mode execution, and neither does RustPython. It is the one
class this toolkit can point to as genuinely novel among the family, the
same way `rustpy-review-toolkit` could point to the `PyAtomicRef`
cast-inconsistency class as its own crown jewel nothing else covers.

**Census, run against the real checkout:** 166 files mention it, 607
textual occurrences, 284 real `if we_are_translated():` branch sites found
via AST (undercounts by whatever's in the 17.4% of files needing the
tree-sitter fallback per §3.1, not yet re-run against those). The 284
split in a way the original plan didn't anticipate: only **59 (21%) have
an `else`/`elif` arm** to diff — the "diff both arms" shape the flagship
agent's design assumed was the norm. **225 (79%) are a bare `if`, no else
at all.**

- **The 79% majority** is one shape every sample matched:
  `if not we_are_translated(): assert ...` — a sanity check that only
  runs under the untranslated test-suite mode. Mechanically recognizable
  (body is only `assert`/`raise` statements) and needs to be an automatic
  default-suppress rule in the scanner itself, not something the agent
  reasons about 225 times a run.
- **The 21% minority** is real, and often more substantial than "debug
  instrumentation" — `rpython/rlib/rgil.py` maintains a whole parallel
  `EmulatedGilHolder` class specifically so GIL logic can be exercised
  under the untranslated test suite before the real translated GIL
  primitives exist. Deliberate, correctly POLICY, and structurally
  different from a bare debug print — the classification section (§5)
  distinguishes these two intentional sub-patterns explicitly rather than
  lumping both under "debug-only instrumentation."

Full trace detail: `pypy-review-toolkit-we-are-translated-census.md`.

### 3.4 Interp-level / app-level boundary (`pypy/interpreter/gateway.py`)

`unwrap_spec`/`interp2app` wrap interpreter-level (RPython) functions for
app-level (the Python program PyPy is running) calling. `gateway.py` itself
runtime-checks some mismatches (confirmed string: `"%s: no match for
unwrap_spec element %s"`), meaning a subset of these bugs surface loudly at
runtime already — the toolkit's job is the subset that doesn't: a raw
Python exception raised in interp-level code reachable from app-level
execution, instead of going through `OperationError`/`space.w_*`, which
behaves correctly only by accident under CPython-hosted testing and may not
survive translation.

**Census, run against `pypy/interpreter/` + `pypy/module/` (non-test):**
107 correct `raise OperationError(...)` sites, 97 raw-builtin-exception
sites — nearly as many as the correct pattern. Traced two individually
rather than assuming the raw count is the bug count:

- `pypy/interpreter/argument.py`, `Arguments.fixedunpack` — **resolved
  clean.** The docstring says "raise a real ValueError," and tracing every
  caller in the non-test tree found none — the only call site at all is
  its own test, which explicitly asserts the `ValueError`. Zero production
  reachability, deliberately tested. ACCEPTABLE, with hard evidence, not a
  hedge.
- `pypy/interpreter/pyframe.py:236`, inside `initialize_frame_scopes` —
  **the strongest candidate found.** The same function has two
  structurally similar internal-consistency checks a few lines apart: one
  correctly uses `oefmt`/`OperationError`, the other raises a raw
  `ValueError`. That in-function asymmetry — the same function using both
  conventions for similar checks — is a much sharper signal than a flat
  raise-site count, and it's checkable statically without a call-graph
  pass. `initialize_frame_scopes` runs from `PyFrame.__init__` — every
  single frame construction in the interpreter.

**Design consequence:** this scanner should lead with the
same-function-asymmetry heuristic (two similar checks, one correct
convention and one not) rather than a flat raise-site census — it found
the strongest real candidate and it's genuinely checkable with a scanner,
no call-graph work required. Full trace detail:
`pypy-review-toolkit-round4-restrictions-and-boundary.md` and
`pypy-review-toolkit-round5-resolved-findings.md`.

### 3.5 RPython-restriction violations: `eval`/`exec` — conclusion

Naive grep for `eval(` gives 96 hits, almost all `.eval()` method calls
unrelated to the builtin. AST-accurate, non-test: 8 real `eval()` calls,
48 real `exec()` calls. Traced every one of the 21 that sit inside a
function body (the ones a flat top-level/inside-function split couldn't
resolve on its own) individually. **All 21 fall into one of two benign
categories**, not a real translation-time violation:

1. **The recognized codegen idiom** — generate a specialized method or
   function once via `exec()`, called at class-body or module-load time,
   before the annotator ever sees the result (`rpython/jit/metainterp/pyjitpl.py`
   stamps out a family of opcode-handler methods this way;
   `rpython/memory/gctransform/refcounting.py` generates a specialized
   deallocator per type the same way, inside the GC-transform pass itself
   — another translation-time tool, not translated runtime code).
2. **Build/translation-tooling reading a spec string** — `rpython/config/parse.py`
   uses `eval()` as a lightweight config-value parser (should probably be
   `ast.literal_eval`, but not an RPython-restriction issue since it never
   runs as translated interpreter code); `rpython/jit/codewriter/support.py`
   uses `eval()` to parse an `oopspec` annotation string at
   codewriter-generation time, also translation-time tooling, not runtime.
   `rpython/annotator/policy.py`'s hit already falls under the existing
   annotator-out-of-scope carve-out (§3.1).

**Conclusion: zero of the 21 candidates are genuine runtime
RPython-restriction violations.** This means `eval`/`exec` specifically is
not a productive check for v0.1 — the real candidate surface for this
scanner, if any, is elsewhere (unbounded `**kwargs`, mixed-type
containers, generators crossing the translation boundary), none of which
have been censused yet. Full trace detail:
`pypy-review-toolkit-round4-restrictions-and-boundary.md`.

### 3.6 Scope: `pypy/module/` — decision needed

Layer sizes, real file/line counts:

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
than `pypy/interpreter` + `pypy/objspace/std` combined, and wasn't named
anywhere in the original scope — not even as an exclusion. That's a real
gap: 3 of the 7 real immutability-contract candidates in §3.2 live inside
`pypy/module/`, found without deliberately targeting it, and
`pyframe.py`'s asymmetry-based finding pattern (§3.4) would apply equally
well there.

In-scope surface without `pypy/module`: **~687,000 lines**
(`rlib`+`jit`+`gc`+`interpreter`+`objspace/std`). With it: **~867,000
lines**. `cpython-review-toolkit`'s entire reviewable surface is ~358,000
lines and needed 37 pre-partitioned review slices. PyPy's is roughly
2–2.4x that scale either way.

**Decision, given the real candidate rate found so far and danzin's
go-ahead to start executing:** `pypy/module/` is **in scope for v0.1**,
not deferred. The evidence in §3.2 and §3.4 already shows real, distinct
candidates inside it, and treating it as out-of-scope by default (rather
than by an explicit, reasoned exclusion) risks missing a meaningful
fraction of what the toolkit is supposed to find. Review-slice
partitioning (a `data/review_slices.json` manifest + slice-tracking
tooling, `cpython-review-toolkit`-style) is therefore a v0.1 component,
not a later refinement — necessary at this scale regardless of the
`pypy/module` decision, but more clearly so once it's included.

### 3.7 GC model

PyPy uses a moving tracing collector (`rpython/memory/gc/`), **not**
reference counting. This is the one dimension where a naive "port
cpython-review-toolkit" instinct would be actively wrong: refcount-auditor,
GIL-discipline-checker, and every finding class built on CPython's manual
refcounting model (the bulk of `cpython-review-toolkit`'s agent roster —
`refcount-auditor`, `ft-race-scanner`'s DECREF-based races,
`uninitialized-dealloc-auditor`) has **no target** here. There is nothing to
transplant from that side of the family at all; the GC-relevant concern
here is narrower — custom trace/finalizer registration for RPython-level
objects holding raw (non-GC-managed) memory, via `rpython/rlib/rgc.py`.

## 4. Surface catalogue

| Class | Mechanism | Static? | Real candidates found | Status |
|---|---|---|---|---|
| **`we_are_translated()` arm divergence** | Two execution modes exercise different branches of the same `if`, never tested together | yes (AST diff of both arms) | 284 branch sites (166 files); 59 two-arm (real diff candidates), 225 single-arm (auto-suppress, §3.3) | **v0.1 flagship — working hypothesis, per danzin** |
| **Immutability-contract mismatch** (`_immutable_fields_`, `@jit.elidable`) | Field/function marked immutable or pure but the class body/call graph shows mutation or a side effect | yes | **7 of 201 checked classes**, all individually traced (§3.2) | **v0.1 crown jewel — strongest-evidenced item in this plan** |
| interp/app boundary leaks | Raw Python exception where `OperationError` is expected, especially same-function asymmetry with a correct sibling check | yes | 97 raw-exception sites censused, 2 individually traced (1 resolved clean, 1 strong candidate — §3.4) | v0.1 |
| RPython-restriction violations (`eval`/`exec` specifically) | Constructs valid as Python but rejected under translation | yes | **0 of 21 inside-function candidates are genuine** — all fall under recognized codegen/build-tooling idiom (§3.5) | v0.1, deprioritized — other restriction shapes (unbounded `**kwargs`, mixed-type containers) not yet censused |
| `@jit.unroll_safe` on an unbounded loop | Loop bound not provably JIT-compile-time-constant | yes, heuristic | not yet censused | v0.1, CONSIDER-capped |
| GC hint discipline (`rgc.no_collect`, custom trace/finalizer) | Function marked non-collecting calls something that may allocate; raw-memory-holding object missing trace registration | yes, narrower than the CPython refcount surface (§3.7) | not yet censused | v0.2 — needs real examples to calibrate before trusting, per §2 |
| JIT driver / merge-point placement | `JitDriver` `greens`/`reds` sanity, `can_enter_jit`/`jit_merge_point` conventions | qualitative | n/a | v0.2, qualitative agent only (see §4.1) |
| Deep JIT backend codegen (`rpython/jit/backend/*/assembler.py`) | Per-architecture assembler emission correctness | effectively no | n/a | explicitly out of scope, not deferred — see §6 |

## 4.1 Components (v0.1)

### Agents

| Agent | Role |
|---|---|
| `pypy-internals-mapper` | preflight — layer-classifies every file (`rlib`/`jit`/`gc`/`annotator-rtyper-translator` [flagged out-of-scope, not silently skipped]/`interpreter`/`objspace`/`module` [in scope, §3.6]), builds the RPython file-set the rest of the toolkit reasons over, and produces the review-slice manifest (below) given the ~687K–867K-line scale. Same role as `rustpy-review-toolkit`'s internals-mapper: single source of truth other scripts import from, not re-derived per script. |
| `translated-divergence-auditor` | **flagship** — `we_are_translated()` arm divergence, with the single-arm auto-suppress rule and the two-arm-emulation-shim POLICY distinction built in from day one (§3.3) |
| `immutability-contract-auditor` | **crown jewel** — `_immutable_fields_`/`@jit.elidable`/quasi-immutable (`?[*]`-ordering-aware) mismatches, with the `__del__`-reassignment case tiered separately from mid-lifetime reassignment (§3.2) |
| `rpython-restriction-scanner` | approximate annotator-restriction violations — `eval`/`exec` deprioritized per §3.5's conclusion; scope narrowed to unbounded `**kwargs`, mixed-type containers, and generators crossing the translation boundary, none censused yet |
| `interp-app-boundary-checker` | `OperationError` leaks, leading with the same-function-asymmetry heuristic (§3.4) rather than a flat raise-site count |
| `jit-trace-reviewer` | qualitative — `JitDriver`/merge-point placement sanity |
| `gc-hint-auditor` | v0.2 — deferred pending real-example calibration per §2 |
| `git-history-analyzer` | reused chassis (§5) |

### Scanners

Following the family's calling convention exactly (`rustpy-review-toolkit`'s
`CLAUDE.md`: every script exposes `analyze(target, *, max_files=0) -> dict`
plus a stdlib-JSON-emitting `main()`, with `analyze_history.py` taking
`argv` as the sole documented exception — that exception is a family
convention to preserve, not a bug to fix here):

- `discover_pypy.py` — checkout detection + `build_pypy_envelope` (mirrors
  `rustpy-review-toolkit`'s `build_rustpy_report`, which the family's own
  `CLAUDE.md` is explicit must be a *local* envelope builder, not a fork of
  the shared `scan_common.build_envelope`)
- `map_pypy_internals.py` — the classification engine (§4.1), single source
  of truth other scanners import from, **plus review-slice manifest
  generation** (`data/review_slices.json`), necessary from v0.1 given the
  real scale in §3.6 — `cpython-review-toolkit`-style, partitioning the
  ~687K–867K-line surface into reviewable chunks rather than assuming
  `explore` can run over the whole tree in one pass
- `scan_translated_divergence.py` — includes the single-arm suppress rule
  and two-arm sub-pattern classification from §3.3 as built-in logic, not
  bolted on after
- `scan_immutability_contracts.py` — includes the `?[*]`-ordering-aware
  marker parser from §3.2 (the exact bug I caught in my own first attempt)
- `scan_rpython_restrictions.py` — narrowed scope per §3.5
- `scan_interp_app_boundary.py` — same-function-asymmetry as primary
  signal per §3.4

### Data

- `data/pypy_non_bugs.md` — calibration/false-positive taxonomy, same
  discipline as `cpython_non_bugs.md`/would-be `rustpy_non_bugs.md`.
  Seeded from day one with the concrete patterns already found: the
  single-arm `assert`-only `we_are_translated()` shape, the codegen-idiom
  `exec()` pattern, `fixedunpack`'s "real ValueError" docstring convention
- `data/jit_hint_contracts.json` — the decorator table from §3.2
- `data/rpython_restricted_builtins.json`
- `data/review_slices.json` — the partition manifest from
  `map_pypy_internals.py`, new in this version given §3.6
- `data/pypy_known_bugs.tsv` — seeded empty at v0.1 pending §2's fuzzing
  question; the schema should exist from day one even if the corpus starts
  at zero, so `known-issues` (§4.1 Commands) has something to grow into

### Commands

`explore` (phased: discovery → history context → flagship +
crown-jewel → boundary/restriction → qualitative → synthesis, mirroring
`cpython-review-toolkit`'s phase table, run per-slice given
`review_slices.json`), `health`, `hotspots`
(divergence + immutability + complexity, "where to look first"),
`known-issues` (present from v0.1 with an empty catalog, per §2 — the
command scaffolding shouldn't be blocked on the catalog existing).

No standalone `map` command — `pypy-internals-mapper`'s output (the layer
classification and slice manifest) is cheap enough to fold into `health`'s
preflight rather than warranting its own top-level command.

## 5. JSON envelope and classification

Same common envelope as the family: `{project_root, scan_root,
<toolkit>_info, functions_analyzed, findings[], summary}`, each finding
`{type, file, line, function, category, classification, confidence,
description, details}`.

**Classification, calibrated for PyPy specifically:**

- **FIX** — an RPython-restriction violation that would fail translation
  outright (highest-confidence class: if the static approximation is
  right, `rpython/bin/rpython` itself would reject it); a `we_are_translated()`
  divergence where the arms are observably inconsistent in control-flow
  shape, not just debug-only instrumentation.
- **CONSIDER** — immutability-contract mismatches (§3.2) and
  `unroll_safe`-on-unbounded-loop findings — these frequently "work" under
  normal JIT compilation and only misbehave under specific trace shapes, so
  they need a human's judgment about actual reachability, exactly the
  posture `rustpy-review-toolkit` gives its own GC-Traverse findings.
- **POLICY** — whether a given `we_are_translated()` divergence is
  intentional debug-only instrumentation vs. a real bug; this pattern is
  common enough in the codebase that the agent should default here unless
  the arm shapes are clearly inconsistent (same anti-false-positive-flood
  posture, echoing how `rustpy-review-toolkit` calibrates "unsafe is the
  norm, don't flag bare blocks").
- **ACCEPTABLE** — `rpython/annotator`, `rpython/rtyper`,
  `rpython/translator` internals, explicitly named as out-of-scope rather
  than silently unmentioned (§3.1).

## 6. Roadmap

**v0.1 (this plan):** `pypy-internals-mapper`,
`translated-divergence-auditor`, `immutability-contract-auditor`,
`rpython-restriction-scanner`, `interp-app-boundary-checker`,
`jit-trace-reviewer` (qualitative), `git-history-analyzer` (vendored). Static
only, `known-issues` scaffolded with an empty catalog.

**v0.2 (deferred, pending real examples per §2):** `gc-hint-auditor`; a
real seed for `pypy_known_bugs.tsv`, contingent on whatever comes of the
fusil-toward-PyPy question; recalibrating `class-level-mutable-attribute`
and any other `code-review-toolkit` checks flagged "mostly, but needs
recalibration" in §1's table, once there's evidence (not just reasoning)
about their PyPy-specific false-positive rate; **a CPython differential
oracle**, per danzin's v0.2 suggestion — the same pattern
`cpython-review-toolkit`'s parity-checker and `rustpy-review-toolkit`'s
proposed CPython differential oracle both use (run equivalent code under
two implementations, treat a behavioral divergence as a confirmed,
localized bug), which applies especially directly here since PyPy's whole
purpose is behavioral parity with CPython. Gated behind having a working
PyPy build to run against, same "needs a built interpreter" caveat
`cpython-review-toolkit` gives its own dynamic checks (`reproduce`,
TSan-analyzer).

**Explicitly out of scope, not deferred (§4, last row):** JIT backend
codegen review. This needs architecture-specific expertise (x86/ARM/zarch
assembler emission correctness) this toolkit's static-approximation
approach has no real way to check, unlike everything else on this list
which is "hard but tractable with the right heuristics." Better to say so
plainly than to carry it as a permanently-stalled roadmap item.

## 7. Vendoring model

Vendor `scan_common.py`, `analyze_history.py`, and `measure_complexity.py`
**verbatim** from `code-review-toolkit` — not from `cpython-review-toolkit`,
whose chassis (`tree_sitter_utils.py`) targets C and has no RPython use, per
the architecture note in §1. If a shared primitive needs changing, change it
upstream in `code-review-toolkit` and sync forward — do not fork the
vendored copy, matching the family-wide rule stated in
`rustpy-review-toolkit/CLAUDE.md`. Two allowed local seams:
`measure_complexity.py`'s `discover_pypy` import as a try/except fallback,
mirroring the pattern `rustpy-review-toolkit` documents for its own
`discover_rustpy` import; and — per §3.1's parsing correction — the
`ast`-then-tree-sitter fallback dispatch itself, which the vendored
`code-review-toolkit` chassis has no reason to know about (it assumes
`ast.parse()` never fails outright on a `.py` file, true for its target
but not for RPython). That dispatch lives in `pypy_utils.py`, not in a
fork of the vendored files — scanners call
`pypy_utils.parse_rpython_file()` rather than `scan_common.parse_source()`
directly.

**Honesty note on `pyo3-review-toolkit`/`rust-ext-review-toolkit`:**
`rustpy-review-toolkit`'s own docs (`README.md`, `CLAUDE.md`, its design
doc) reference these as real sibling tools — the transfer-gradient
experiment in §1 and the vendoring pattern above are both sourced directly
from those files, not invented. But both repos are private — `git clone`
against them fails with an auth error, and neither appears in danzin's
public repo list. So everything cited about them here is only as accurate
as what `rustpy-review-toolkit` says about them secondhand, not
independently verified against their actual source the way every other
claim in this document is. Flagging this rather than letting it pass as
equally well-grounded as the rest.

## 8. Execution plan (v0.1 — DONE)

Steps 1-9 below are complete: plugin skeleton, chassis vendored, all four
scanners built and validated against the real checkout with real bugs
found and fixed along the way (see each scanner's module docstring for
its own validation history), all 6 agents, all 4 commands, `pypy_non_bugs.md`
seeded from real findings, 35 tests passing, 4 commits. Kept here as the
historical record of what v0.1 actually built, not as a forward-looking
plan anymore — §9 is the current plan.

1. **Plugin skeleton** — `.claude-plugin/plugin.json`, marketplace.json,
   README, directory layout per §4.1. ✅
2. **Vendor the three chassis scripts** from `code-review-toolkit`
   verbatim, per §7. ✅
3. **`discover_pypy.py` + `pypy_utils.py`** — the `ast`/tree-sitter
   dispatch from §3.1. ✅ (needs the `lib_pypy/` scope addition and
   branch/version reporting from §0.2/§9.3 — not yet done)
4. **Layer classification + review-slice manifest** — folded into
   `discover_pypy.py` directly rather than a separate `map_pypy_internals.py`. ✅
5. **`scan_translated_divergence.py` + `translated-divergence-auditor`**
   — flagship (working hypothesis, reassessed in §9). ✅
6. **`scan_immutability_contracts.py` + `immutability-contract-auditor`**
   — crown jewel (reassessed in §9). ✅
7. **`scan_interp_app_boundary.py` + `interp-app-boundary-checker`** —
   same-function-asymmetry as the primary signal. ✅
8. **`scan_rpython_restrictions.py`** — narrowed scope, `**kwargs` only,
   `eval`/`exec` deliberately excluded. ✅
9. Remaining agents, commands, `data/pypy_non_bugs.md`. ✅

## 9. What the fuzzer data changes (v0.2 plan — current)

danzin's go-ahead was explicit: "Feel free to start implementing it." This
section is what "it" now means, given §0's real evidence — not a
hypothetical roadmap item anymore.

### 9.1 New scanner: `scan_unvalidated_helper_calls.py` (Shape A)

**Priority: highest.** Two of five real fuzzer-confirmed bugs match this
shape exactly, more than any other single shape, and it's the one shape
with a genuinely strong, narrow, checkable signature: a call to a
specific RPython helper function known to have an unstated-in-signature
precondition, from code reachable via `unwrap_spec`/`interp2app`, with no
range/type check in between.

**v0.1 approach — curated list, not general taint analysis.** Real
data-flow analysis (tracing an app-level value through arbitrary
intermediate variables to a helper call) is a much bigger undertaking than
anything else in this plan. Start narrower and honest about the
narrowness: maintain `data/sensitive_rpython_helpers.json`, a curated list
of helpers with known unstated preconditions (seeded with exactly the two
confirmed: `rutf8.unichr_as_utf8` — needs `code <= 0x10FFFF`, non-surrogate
unless `allow_surrogates=True`; `rbigint.tobytes`/`tobytes_int` — needs
`signed` to actually match the value's sign). For each, flag any call site
in `pypy/module/*` where:
- the call is inside a function reachable from `interp2app` (has an
  `unwrap_spec`-decorated caller, or is itself such a function), and
- no `if`/`assert` checking the relevant precondition (range comparison
  for the codepoint case, sign check for the bigint case) appears between
  the value's introduction and the call, in the same function.

This will under-report (real precondition checks a few calls deep, or
via a helper function, won't be seen) and the list starts at 2 entries —
both limitations should be stated in the scanner's own docstring, the
same honesty standard `scan_rpython_restrictions.py` already holds for
`eval`/`exec`. Growing the list is the main way this scanner gets more
valuable over time; it should be easy to add an entry from a future
fuzzer finding.

**Dynamic oracle, worth building alongside if there's ever a way to run
candidate inputs**: danzin's own report notes `"unexpected internal
exception (please report a bug)"` in stderr is a reliable signal PyPy hit
this bug class. Out of scope for a static-only v0.1 scanner, but worth
keeping in mind if this toolkit ever gains an execution component.

### 9.2 New scanner: `scan_sibling_guard_consistency.py` (Shape D)

**Priority: high.** One confirmed bug (`_dealloc_warn_w`), but the pattern
is exactly the kind of thing `interp-app-boundary-checker`'s
same-function-asymmetry heuristic already proved works well as a signal
— this is the same idea, generalized from "within one function" to
"across sibling methods on one class."

**Approach**: for each class, find methods that guard access to a
particular `self.*` field with a recognizable pattern —
`self._check_attached(...)`, `self._check_closed(...)`, `if self.X is
None: raise/return`, or a call to a method whose own name suggests a
validity check (`_check_*`). Build the set of fields each such guard
appears to protect (best-effort: the field(s) referenced in the guarded
method's remaining body). Then, for every *other* method on the same
class that touches one of those same fields, check whether it also
invokes an equivalent guard. Flag the ones that don't.

This is a real, novel scanner idea beyond anything else in this plan's
surface catalogue — worth building even though it only has one confirmed
instance so far, because the shape itself (a sibling method missing the
guard everything else on its class has) is a general pattern PyPy's own
wrapper-heavy `pypy/module/_io/`-style code plausibly has more instances
of, not just this one.

### 9.3 Scope addition: `lib_pypy/`

Confirmed real and present in the checkout (unlike `_lzma.py`
specifically, see §0.2): `lib_pypy/gdbm.py` and `lib_pypy/_pypy_util_cffi.py`
both call `free()`. This is PyPy-specific hand-written implementation code
(cffi-based replacements for CPython C extensions), not the CPython-
compatible `lib-python/` stdlib that's explicitly out of scope — it should
be a new in-scope layer in `discover_pypy.py`, separate from
`pypy/module` since its bug shapes (raw `free()` pairing, cffi attribute
typos) are different from anything `pypy/module`'s existing scanners
check for.

**New narrow scanner, `scan_free_pairing.py` (Shape B + C combined,
scoped to `lib_pypy/` only)**: for every file under `lib_pypy/` that calls
`.free(` (small, checkable set — currently 2 files in this checkout), flag
(a) any `ffi.<name>` attribute access where `<name>` isn't in a
conservative known-good allowlist (catches the `ffi.NONE`-doesn't-exist
shape), and (b) any `free()` call not visibly guarded by the same lock
object as other `free()`/mutation calls on the same field within the same
class (catches the unlocked-check-then-free shape). Both narrower and
more speculative than 9.1/9.2 — one confirmed bug covers both sub-shapes
combined, not two separate confirmed instances — so this is CONSIDER-only
output, explicitly lower-priority than 9.1/9.2 in the build order below.

### 9.4 Branch/version pinning

Per §0.2: `discover_pypy.py`'s envelope should report the checked-out git
branch and commit, and — where derivable — a PyPy version string (check
`pypy/tool/version.py` or equivalent), so every report says plainly what
it was actually run against. Before trusting any of §§1-8's existing
numbers as current, spot-check them against whatever branch/tag actually
corresponds to what's being fuzzed or reviewed next — `main` may not be
it.

### 9.5 Flagship/crown-jewel status: not abandoned, not confirmed either

Stated plainly rather than left implicit: `translated-divergence-auditor`
and `immutability-contract-auditor` remain built, tested, and real —
every finding either produces is grounded in individually-traced source,
not guessing. But neither has a single fuzzer-confirmed instance behind
it, while the two new shapes above now have real crashes/leaks each. This
doesn't mean deprioritizing the existing scanners' *output* — it means
9.1 and 9.2 are the next things to *build*, ranked above any further
polish on the existing four, because they're the first checks this
project can point to a real bug and say "this shape, specifically, is why
this check exists."

### 9.6 Updated build order

1. `scan_unvalidated_helper_calls.py` + `data/sensitive_rpython_helpers.json`
   (§9.1) — highest priority, two confirmed bugs.
2. `scan_sibling_guard_consistency.py` (§9.2) — one confirmed bug, strong
   generalizable pattern.
3. `discover_pypy.py` branch/version reporting + `lib_pypy/` as a new
   in-scope layer (§9.3/§9.4) — infrastructure the narrow free-pairing
   scanner needs, and good practice regardless.
4. `scan_free_pairing.py` (§9.3) — narrower, `lib_pypy/`-scoped, lower
   priority than 1-2.
5. Update `git-history-analyzer` to specifically look for the
   incomplete-fix-across-wrapper-layers pattern (Shape E) when reviewing
   fix commits — small addition to an agent that already exists rather
   than a new component.
6. Re-verify `pypy_known_bugs.tsv`'s file/line references against whatever
   branch actually matches PyPy 7.3.23, once §9.4's branch reporting makes
   that checkable.

Fusil-on-PyPy is still running on danzin's side — this build order should
absorb whatever comes next the same way it absorbed this first batch:
real bugs first, then check whether they match an existing shape or need
a new one.
