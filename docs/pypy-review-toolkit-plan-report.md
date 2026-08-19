# pypy-review-toolkit — Plan Report

*Prepared by Bhuvansh. Both sibling tools were cloned locally and read
directly (source, tests, design docs) rather than summarized from READMEs.
PyPy itself (`pypy/pypy`, GitHub mirror) was also cloned and used to verify
every PyPy-specific claim below against real files.*

---

## Part 1 — What we already have

### 1.1 `cpython-review-toolkit`

**What it is:** a Claude Code plugin that reviews CPython's own C runtime —
not extensions, the interpreter itself — for the bug classes generic C
tools (clang-tidy, cppcheck) don't understand: refcounting, GIL discipline,
CPython's error-handling convention, PEP 7 style.

**Scale:** 23 analysis agents, 7 commands, ~960KB of analysis scripts.

**Architecture — two generations of scanner coexist:**

| Generation | Targets | How |
|---|---|---|
| Legacy regex scanners | refcounts, error-paths, null-safety, GIL, complexity, PEP 7, includes | stdlib-only regex — works because PEP 7 C is extremely regular |
| Tree-sitter crash-class detectors | recursion-guard gaps, destructor exception-clobber, uninitialized-dealloc, init-bypass, free-threading races (3 scanners), lock discipline | real C syntax tree via `tree_sitter_utils.py`, so they track true function boundaries and call graphs, not line patterns |

**Standout features beyond plain bug-scanning:**
- **Differential parity checker** (`find_parity_pairs.py`) — CPython ships
  C accelerators next to pure-Python fallbacks (`_decimal`/`_pydecimal`,
  `_io`/`_pyio`, `_datetime`/`_pydatetime`). Where the C version crashes but
  the Python twin raises cleanly, that's a confirmed, localized bug —
  CPython ships its own oracle for free.
- **OOM reproducer** (`run_oom_sweep.py` / `reproduce` command) — turns a
  static allocation-failure candidate into an actually-reproduced crash via
  dense `_testcapi.set_nomemory` sweeps on a locally built CPython. A
  non-reproduction is reported honestly as inconclusive, not as a
  refutation.
- **`known-issues` regression command** — cross-references a seed catalog
  of previously-found CPython crashes (from fusil OOM/TSan findings + the
  tracker) against a fresh scan, with drift-tolerant classification
  (`present` / `line_drifted` / `absent_in_function` / `absent` /
  `file_missing`).
- **`informed-explore`** — a catalog-seeded targeted pass: builds a briefing
  from a bug-shape catalog (each shape + its "guarded twin" + a hunt
  directive) plus a false-positive taxonomy, so a re-review hunts
  un-found siblings of known defect shapes instead of re-deriving from
  scratch.
- **Review-slice partitioning** — the reviewable CPython surface (188
  files, ~358,000 lines under `Objects/`/`Modules/`) is pre-partitioned
  into 37 slices of ≤13,000 lines each, because a 39,800-line slice was
  measured to strain a single review pass while a 13,250-line slice
  triaged well. Tooling (`slice_status.py`, `make_slice_context.py`) tracks
  campaign progress slice-by-slice.

**Classification:** every finding is FIX / CONSIDER / POLICY / ACCEPTABLE.
Scripts find candidates (10–50% false-positive rate is expected and
accepted); agents read the real code and confirm or dismiss.

**Explore pipeline is phased**, not flat: discovery → structural + temporal
context → safety-critical agents → crash-class detectors → memory safety →
free-threading → code quality → maintenance → parity → history-last →
synthesis.

### 1.2 `rustpy-review-toolkit`

**What it is:** reviews the RustPython interpreter's own Rust source —
again the runtime itself, explicitly *not* PyO3 and *not* extensions built
with RustPython.

**Scale:** 6 agents, 4 commands — deliberately smaller than
`cpython-review-toolkit`, and the design doc explains why: it's v0.1 of a
younger project, with a v0.2 roadmap already scoped.

**The single most important thing in this toolkit's design doc — a
transfer-gradient experiment:** before building anything native, the team
ran two *existing* sibling toolkits (built for PyO3 extensions) against
RustPython directly, because RustPython shares PyO3's vocabulary
(`PyRef`, `PyResult`, `#[pyclass]`, `.downcast()`). Result:
- The PyO3-*implementer* scanner keyed on `unsafe { ffi::<CPython C-API> }`
  — a substrate RustPython doesn't have — and correctly produced nothing.
  Clean true-negative.
- The PyO3-*consumer* scanner keyed on idioms RustPython uses under the
  **same names but different semantics**, and mis-fired badly: 4 false
  `non_send_field` findings on RustPython's own `#[pyclass]` types, 269
  false `renamed_api` findings on RustPython's own `.downcast()`. Agent
  triage did **not** rescue these, because the check logic itself encoded
  PyO3 semantics.

**Conclusion the design doc draws, verbatim in spirit:** *"RustPython's
real bug classes are native, not transplantable."* Every check in the
shipped toolkit is built from RustPython's own object model, not ported
from a sibling that merely looks similar.

**Two research inputs, reconciled explicitly, not just merged:** a fuzzing
defect report (24 confirmed findings across 10 classes) and a static
cross-application experiment (the one above). They *disagreed* about
priority — the fuzzing report never examined GC-Traverse-completeness at
all; the static experiment missed the Python-reachable panic class
entirely, which the fuzzing report found to be the single biggest bug
class (12 of 24 confirmed findings). The design doc has a literal
merit-assessment table reconciling every surface, accepting some findings,
rejecting others as over-weighted, and deferring the rest to v0.2 with
reasons stated per item.

**Flagship + crown jewel, chosen because they're validated, not
theoretical:**
- **Panic-site auditor** (flagship) — Python-reachable `.unwrap()`/`.expect()`
  and index/arity out-of-bounds. Confirmed by the fuzzing report as the
  dominant real bug class.
- **Unsafe-soundness auditor** (crown jewel) — a specific cross-method
  pointer-cast inconsistency in `PyAtomicRef` that caused a real SIGSEGV,
  independently flagged by both research inputs.
- **GC-Traverse auditor** — shipped, but explicitly capped at CONSIDER,
  never FIX, and labeled "real surface, 0 fuzzer-confirmed instances"
  because nothing has validated it against an actual bug yet.

**Reachability tiering is the core discriminator**, not a nice-to-have:
every function is classified `py` (Python-reachable) > `protocol`
(protocol-trait-reachable) > `internal`, and `internal`-tier findings are
default-silenced. This is what keeps the panic scanner from drowning
reviewers — RustPython's internals contain thousands of `.unwrap()` calls;
almost all of them are fine, and the tier is what separates signal from
noise.

**Vendoring discipline:** five shared "chassis" scripts are vendored
**verbatim** from the sibling `rust-ext-review-toolkit` and must never be
forked locally — if a shared primitive needs to change, it changes
upstream and syncs forward. Only two narrow, explicitly-listed local seams
are allowed.

### 1.3 What both toolkits agree on (the family's shared shape)

- Every scanner emits a common JSON envelope: `{project_root, scan_root,
  <toolkit>_info, functions_analyzed, findings[], summary}`.
- Every finding carries `{type, file, line, function, category,
  classification, confidence, description, details}`.
- Classification is always FIX / CONSIDER / POLICY / ACCEPTABLE.
- Scripts find *candidates*; an agent reads real code and confirms/dismisses.
- A false-positive taxonomy doc (`*_non_bugs.md`) exists per toolkit and is
  actively maintained, not an afterthought.
- Commands follow a consistent shape: `explore` (full, phased), `health`
  (dashboard), `hotspots` (where to look first), plus toolkit-specific
  extras (`known-issues`, `reproduce`, `informed-explore`, `map`, `migrate`).
- The parsing layer is chosen to match the *actual implementation
  substrate*, never the conceptually-nearest sibling: C → tree-sitter-c,
  Rust → tree-sitter-rust. Neither toolkit reuses a language-mismatched
  chassis just because the target is "also a Python runtime."

---

## Part 2 — What that means for PyPy

### 2.1 The one architectural decision this settles immediately

RPython is not a separate grammar. It's Python source — a restricted,
statically-typed-by-annotation subset of Python 2 — and every file under
`rpython/` and `pypy/interpreter`/`pypy/objspace` parses cleanly with the
standard library `ast` module (verified directly against the real
checkout). So per the family's own rule ("match the substrate, not the
conceptual sibling"), this toolkit's parser is `ast`, not tree-sitter. It's
architecturally closer to `code-review-toolkit` (plain Python, `ast`-based)
than to either C/Rust sibling, even though *conceptually* it's closest to
`cpython-review-toolkit` (both review a Python runtime's own
implementation).

### 2.2 Applying RustPython's transfer-gradient lesson to PyPy

Before assuming anything transfers, I sampled `code-review-toolkit`'s
existing 35-check pitfall registry (`scan_python_pitfalls.py`) against what
RPython source actually contains:

| Check | Verdict |
|---|---|
| `late-binding-closure-in-loop` | Transfers cleanly — Python closure semantics are identical in RPython. |
| `mutable-default-argument` | Needs recalibration — RPython's annotator has its own rules here, and the more common RPython-specific version of this hazard is a mutable *class* attribute, not a mutable default arg. |
| `class-level-mutable-attribute` | Needs full RPython-native rewrite, not a tuned copy — its real meaning is governed by the annotator/JIT's immutability contracts (`_immutable_fields_`, see below), which general-Python semantics know nothing about. |
| `asyncio-fire-and-forget-task`, `blocking-call-in-async-function`, `unawaited-coroutine`, `lru-cache-on-method`, `mock-callable-as-spec` | Don't apply — RPython has no `async`/`await`, and `functools.lru_cache`/`unittest.mock` aren't RPython-safe usage in the reviewed surface. Should return empty on real input; **worth confirming empirically before trusting**, since a check that's supposed to be silent and isn't is itself a bug. |

**Conclusion, matching RustPython's own finding:** don't transplant
wholesale. Vendor what transfers, rewrite what needs RPython's own
semantics, and empirically verify what should just be silent.

### 2.3 PyPy-specific architecture, verified against real source

**RPython is Python, checked by the annotator, not the parser
(`rpython/annotator/model.py`).** The restrictions (no `eval`, no unbounded
polymorphic containers, etc.) are enforced at translation time, which
takes minutes — this toolkit's job is a fast static *approximation* of
what the annotator would reject, not a replacement for actually running it.

**JIT hints (`rpython/rlib/jit.py`)** — confirmed decorator set:
`elidable`, `unroll_safe`, `dont_look_inside`, `promote`, `promote_string`,
`promote_unicode`, `loop_invariant`, `elidable_promote`, `purefunction`,
`purefunction_promote`, `isvirtual`, `loop_unrolling_heuristic`,
`conditional_call_elidable`. `@jit.elidable` is a purity contract (no side
effects, result determined by args alone); `@jit.unroll_safe` needs a
JIT-compile-time-bounded loop or risks trace explosion.

`_immutable_fields_` is where this gets concrete — real usage pulled
straight from the checkout:
- `pypy/objspace/std/complexobject.py`: `_immutable_fields_ = ['realval', 'imagval']`
- `pypy/objspace/std/dictmultiobject.py`: `_immutable_fields_ = ["mstrategy?"]`
  — the trailing `?` marks a field as *quasi*-immutable (assigned once
  after construction), not truly const.

A mismatch here — a field marked immutable that the class body actually
mutates, or a quasi-immutable field missing its `?` — is either a
JIT-correctness bug (the JIT folds a stale value) or a missed optimization.
This is the strongest candidate for a crown-jewel agent.

**`we_are_translated()` — the one bug class with no analog in either
sibling toolkit.** Defined in `rpython/rlib/objectmodel.py`. PyPy code
routinely branches on this predicate: one arm runs only when interpreted
directly under CPython (the fast test-suite path), the other only once
translated to C. By construction, the two arms are never exercised by the
same test run. Neither CPython nor RustPython has an analogous dual-mode
execution split — this is genuinely PyPy-native, the same way
RustPython's `PyAtomicRef` cast-inconsistency class was RustPython-native.
**Proposed flagship.**

**Interp-level / app-level boundary (`pypy/interpreter/gateway.py`)** —
`unwrap_spec`/`interp2app` wrap RPython functions for calling from the
Python program PyPy is running. `gateway.py` already runtime-checks some
mismatches (confirmed literal string in the source: `"%s: no match for
unwrap_spec element %s"`) — so the toolkit's job is the subset that
doesn't surface loudly: a raw Python exception raised in interp-level code
reachable from app-level, instead of the `OperationError`/`space.w_*`
convention.

**GC model — the one place a naive port would be actively wrong.** PyPy
uses a moving tracing collector (`rpython/memory/gc/`), not reference
counting. That means the bulk of `cpython-review-toolkit`'s agent roster —
refcount-auditor, GIL-discipline-checker, DECREF-based race detection —
has literally nothing to target here. There's no transplant on that axis
at all; the narrower equivalent concern is custom trace/finalizer
registration for raw-memory-holding objects via `rpython/rlib/rgc.py`,
deferred to v0.2 pending real examples (see §2.5).

### 2.4 Proposed design

**Identity:** reviews PyPy's own RPython-level implementation
(`rpython/`, `pypy/interpreter`, `pypy/objspace`) for correctness bugs and
translation hazards. Not for code that runs *on* PyPy; not for the
CPython-compatible stdlib PyPy ships (`lib-python/`, `lib_pypy/`).

**Surface catalogue:**

| Class | Static? | Status |
|---|---|---|
| **`we_are_translated()` arm divergence** | Yes (AST diff of both arms) | **v0.1 flagship** |
| **Immutability-contract mismatch** (`_immutable_fields_`, `@jit.elidable`) | Yes | **v0.1 crown jewel** |
| RPython-restriction violations (approximating what the annotator would reject) | Yes, approximate | v0.1, explicitly labeled approximate |
| interp/app boundary leaks (`OperationError` vs. raw exceptions) | Yes | v0.1 |
| `@jit.unroll_safe` on an unbounded loop | Yes, heuristic | v0.1, CONSIDER-capped |
| GC hint discipline (`rgc.no_collect`, trace/finalizer registration) | Yes, narrow | v0.2 — needs real examples first |
| JIT driver / merge-point placement sanity | Qualitative | v0.2, qualitative only |
| Deep JIT backend codegen (`rpython/jit/backend/*/assembler.py`) | Effectively no | **explicitly out of scope**, not deferred |

**Agents (v0.1):** `pypy-internals-mapper` (preflight, layer-classifies
every file), `translated-divergence-auditor` (flagship),
`immutability-contract-auditor` (crown jewel), `rpython-restriction-scanner`,
`interp-app-boundary-checker`, `jit-trace-reviewer` (qualitative),
`git-history-analyzer` (vendored chassis).

**Scanners:** same calling convention as both siblings —
`analyze(target, *, max_files=0) -> dict` plus a JSON-emitting `main()`,
with `analyze_history.py` keeping the family's one documented exception
(`argv`-based). `discover_pypy.py` for checkout detection + envelope,
`map_pypy_internals.py` as the single-source-of-truth classification
engine other scanners import from (mirroring both siblings' internals
mappers), then one scanner per agent above.

**Commands:** `explore` (phased, same shape as both siblings), `health`,
`hotspots`, `known-issues` — scaffolded from v0.1 even with an empty
catalog, so it has somewhere to grow into. No standalone `map` command;
folded into `health`'s preflight since PyPy's directory structure doesn't
need `cpython-review-toolkit`'s dedicated include-graph tooling.

**Classification**, calibrated for PyPy: FIX = a restriction violation that
would actually fail translation, or a clearly-inconsistent
`we_are_translated()` divergence. CONSIDER = immutability-contract
mismatches and unroll-safety findings (often "work" until a specific trace
shape breaks them). POLICY = `we_are_translated()` divergence that's
plausibly intentional debug-only instrumentation — this pattern is common
enough that the agent should default here unless arm shapes are clearly
inconsistent, to avoid drowning reviewers. ACCEPTABLE =
annotator/rtyper/translator internals, named explicitly as out-of-scope
rather than silently skipped.

**Vendoring:** `scan_common.py`, `analyze_history.py`,
`measure_complexity.py` vendored **verbatim** from `code-review-toolkit`
(not from `cpython-review-toolkit`, whose chassis is C-only and has no
RPython use). Changes go upstream and sync forward, matching the
family-wide rule. One allowed local seam: a try/except `discover_pypy`
import fallback in `measure_complexity.py`, mirroring the pattern both
Rust-side toolkits already use.

### 2.5 The one real gap — stated plainly, not glossed over

Both mature sibling toolkits earned their FIX/CONSIDER confidence from a
real, confirmed-bug catalog — `cpython-review-toolkit`'s
`cpython_known_bugs.tsv` (seeded from fusil OOM/TSan findings + the
tracker) and `rustpy-review-toolkit`'s `known_panics.tsv` (seeded from a
fuzzing campaign). **This plan currently has zero confirmed PyPy bugs
behind any proposed check.** fusil's actively-developed path targets
CPython specifically (`fusil-python-threaded`); there's no existing PyPy
fuzzing corpus to seed a catalog from.

Two honest, non-exclusive options:
1. Ship v0.1 static-only, explicitly labeled unvalidated — same posture
   `rustpy-review-toolkit` gives its own GC-Traverse checker (capped at
   CONSIDER, never FIX, "real surface, 0 fuzzer-confirmed instances")
   until something confirms it.
2. Extend fusil toward PyPy in parallel. Per the earlier chat, PyPy
   maintainers already said they'd accept "a clear report for relevant
   crashes with simple reproducers" — a much lower bar than asking for
   dedicated fuzzing infrastructure. Even a handful of confirmed PyPy
   crashes would do for this toolkit what the fuzzing report did for
   `rustpy-review-toolkit`: tell us which proposed bug class is real
   before we build 5 agents around it.

---

## Part 3 — Open questions for danzin

1. **fusil-toward-PyPy sequencing** — worth doing before this toolkit, in
   parallel, or not a priority right now? This directly affects how much
   confidence v0.1's findings can honestly claim.
2. **Is `translated-divergence-auditor` really the right flagship?** It's
   the most PyPy-native bug class by construction, but that's reasoning
   from reading the code, not from having hit it in practice the way the
   RustPython panic class was validated by an actual fuzzing report. Is
   there a bug class you've hit working on PyPy that should lead instead?
3. **Repo home** — under my (Bhuvansh's) GitHub account, you added as a
   contributor, per what we discussed.
4. **Scope check on what's explicitly out of scope** — JIT backend codegen
   review (`rpython/jit/backend/*/assembler.py`) is marked permanently out
   of scope rather than deferred, since it needs architecture-specific
   expertise a static approximation can't really approximate. Agree, or is
   there a narrower slice of that worth attempting later?

---

*Full technical design doc (architecture primer with file/line citations,
complete component spec, JSON envelope, roadmap) is in a companion file,
`pypy-review-toolkit-design.md`, for reference once we're past the
go/no-go on direction.*
