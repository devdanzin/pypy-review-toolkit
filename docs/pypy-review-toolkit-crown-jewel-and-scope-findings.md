# pypy-review-toolkit — Crown-Jewel Census + Scale/Scope Findings

*Third round of investigation, going deeper per your request. Two separate
things here: (1) the same empirical treatment the flagship got, now applied
to the crown-jewel immutability-contract check, including one real
candidate worth danzin's own eyes; (2) a scope gap the design doc has been
carrying silently — `pypy/module/` is unaccounted for and it's not small.*

## 1. Crown jewel: `_immutable_fields_` / `@jit.elidable` census

**Real surface, comparable in scale to the flagship:** 268
`_immutable_fields_` declarations across 98 files, 183 `@jit.elidable`
call sites.

**Built and ran the actual mismatch heuristic** — for every class
declaring `_immutable_fields_`, checked whether any field is assigned
in a method other than `__init__` (which would violate immutability if
the field has no `?` quasi-immutable marker, or is expected/benign if it
does).

First pass over-counted: the naive version flagged 24 of 201 classes, but
manually inspecting the top hits (`pypy/objspace/std/mapdict.py`,
`rpython/rlib/rvmprof/rvmprof.py`) showed several were already correctly
marked quasi-immutable (`ever_mutated?`, `is_enabled?`) — my own heuristic
wasn't distinguishing "properly marked quasi-immutable, mutation is
expected" from "claims full immutability but gets mutated anyway." Fixed
that distinction and re-ran:

| | Count |
|---|---|
| Classes with `_immutable_fields_`, checked | 201 |
| Field has `?`, mutated outside `__init__` (**expected, not interesting**) | 18 |
| Field has **no** `?`, mutated outside `__init__` (**real candidates**) | **9** |

### The strongest one: `pypy/interpreter/pycode.py`, `PyCode.co_filename`

```python
# line 58-59
_immutable_fields_ = ["_signature", "co_argcount", ..., "co_filename", ...]
# no '?' -- this claims co_filename never changes after construction

# line 86, inside __init__
self.co_filename = filename

# line ~161, inside a later method (freezing the filename for translation)
# ...
self.co_filename = '<builtin>/%s' % (basename,)
```

`co_filename` is set once in `__init__`, then set **again**, later, in a
different method — one whose own comment says *"When translating PyPy,
freeze the file name ... instead of freezing the complete translation-time
path."* That's a real second assignment to a field declared fully
immutable, and the surrounding comment confirms it's deliberate — this
isn't a typo, it's a genuine translation-time rewrite of a field the class
also tells the JIT never changes.

**I'm not asserting this is a live JIT-correctness bug** — that depends on
whether any `@jit.elidable`-decorated code could read/cache `co_filename`
before this freeze step runs during translation, which needs someone who
actually knows PyPy's translation pipeline timing, not a static heuristic.
But structurally, this is *exactly* the defect shape the crown-jewel
agent's whole premise describes, found on the first real pass, in a
central file (`pycode.py` — every compiled Python function's code object).
Worth flagging to danzin directly, both as a possible real finding and as
validation that the heuristic finds real things, not just false positives.

**Other 8 candidates**, same shape (field with no `?`, real second
assignment outside `__init__`), not yet individually inspected:
`pypy/module/_rawffi/alt/interp_struct.py` (`W__StructInstance.rawmem`),
`pypy/module/_cppyy/interp_cppyy.py` (`CPPMethod`, 4 fields),
`pypy/module/_cffi_backend/realize_c_type.py` (`W_RawFuncType`, 3 fields),
`pypy/module/struct/interp_struct.py` (`W_Struct.format`/`.size`),
`pypy/module/__builtin__/interp_classobj.py` (`W_ClassObject.bases_w`),
`pypy/interpreter/generator.py` (`GeneratorIterator.pycode`),
`pypy/interpreter/function.py` (`Function.closure`/`.defs_w`),
`pypy/objspace/std/mapdict.py` (`UnboxedPlainAttribute`, 2 fields). Several
of these are in `pypy/module/` (see §2 below on why that's notable).

**Conclusion for the crown jewel:** the same discipline that mattered for
the flagship applies here too — the naive heuristic needs the
already-marked-quasi-immutable exclusion or it drowns real candidates in
2x noise (24 raw hits → 9 real ones). With that fix, real candidate rate
is 4.5% of all classes carrying the contract (9/201), concentrated enough
to be worth an agent's attention without flooding a review.

## 2. Scope gap: `pypy/module/` is unaccounted for, and it's the biggest single directory

Ran file/line counts across every layer the design doc discusses, plus the
one it doesn't:

| Directory | Files | Lines |
|---|---|---|
| `rpython/rlib` | 253 | 391,827 |
| `rpython/jit` | 635 | 190,682 |
| `rpython/memory/gc` | 22 | 13,538 |
| `rpython/annotator` (out of scope, §5) | 24 | 12,816 |
| `rpython/rtyper` (out of scope, §5) | 114 | 50,849 |
| `rpython/translator` (out of scope, §5) | 172 | 39,012 |
| `pypy/interpreter` | 124 | 44,327 |
| `pypy/objspace/std` | 96 | 45,916 |
| **`pypy/module`** | **820** | **179,994** |

`pypy/module/` — the built-in module implementations (`_socket`, `struct`,
`thread`, `_cffi_backend`, `_rawffi`, `_cppyy`, `micronumpy`, and dozens
more) — is **820 files and ~180,000 lines, bigger than
`pypy/interpreter` + `pypy/objspace/std` combined**, and the current
design doc's Project Identity section (§1) only names `rpython/`,
`pypy/interpreter`, and `pypy/objspace` as the review target. It's not
explicitly excluded either — it's just never mentioned, which is worse
than an explicit exclusion, because it means the scope is silently
undersized relative to what "PyPy's own implementation" actually means.
And it's not a marginal omission: 3 of the 9 real immutability-contract
candidates above live inside `pypy/module/`, found without even targeting
it deliberately.

**In-scope surface, doing the arithmetic honestly:**
`rlib` (392K) + `jit` (191K) + `gc` (14K) + `interpreter` (44K) +
`objspace/std` (46K) = **~687,000 lines**, even *before* deciding on
`pypy/module`. Add `pypy/module` and it's **~867,000 lines**. For
comparison, `cpython-review-toolkit`'s entire reviewable surface
(`Objects/` + `Modules/`) is ~358,000 lines, and that already needed
37 pre-partitioned slices of ≤13,000 lines each because a single
39,800-line slice was measured to strain one review pass. PyPy's in-scope
surface is roughly **double CPython's, even under the narrower scope**,
and closer to **2.4x** if `pypy/module` is included.

## 3. What this changes in the design

- **§2 (Project identity / scope) needs an explicit decision on
  `pypy/module`**, not silence. Two honest options: (a) in scope for v0.1,
  since it's real RPython code with the exact same bug classes (as §1's
  finding already shows) and excluding it means missing a third of all
  real candidates found so far; or (b) explicitly deferred to v0.2 with a
  stated reason (e.g. "built-in modules individually vary too much in
  quality/idiom to calibrate a single false-positive baseline against, do
  the core interpreter/objspace first"). Either is defensible — silence
  isn't.
- **A review-slice partitioning scheme, `cpython-review-toolkit`-style, is
  needed from v0.1, not a nice-to-have.** At ~687K–867K lines, running
  `explore` against the whole tree in one pass isn't viable — this needs
  the same kind of pre-partitioned slice manifest
  (`data/review_slices.json`) and slice-tracking tooling
  (`slice_status.py`/`make_slice_context.py` equivalents)
  `cpython-review-toolkit` built out of necessity at a smaller scale. This
  wasn't in the v0.1 component list at all; it should be, given the real
  numbers.
- **Crown jewel confirmed viable with real evidence**, not just reasoning
  — a concrete, plausible candidate finding in a central file
  (`pycode.py`), at a workable signal rate (9 real candidates from 201
  checked classes) once the heuristic correctly separates quasi-immutable
  from fully-immutable claims.

## 4. Still open, next round if useful

- Individually inspect the other 8 candidates the way `pycode.py` got
  inspected, particularly the `pypy/module/_cppyy/interp_cppyy.py` one
  (4 fields flagged on one class, the largest single candidate) — cppyy
  wraps C++ objects and might have a legitimately different
  initialization-timing story worth understanding before treating it the
  same as the others.
- RPython-restriction-violation census (eval/exec/unbounded-kwargs/mixed-type-container
  frequency) — same treatment as §1 and the flagship census, not yet done.
- interp/app boundary raw-exception census inside `pypy/module/` and
  `pypy/interpreter/` — how many raw `raise ValueError`/`TypeError` sites
  exist in app-reachable code vs. going through `OperationError`.
