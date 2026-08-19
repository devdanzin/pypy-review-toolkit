# PyPy Review Toolkit — Known Non-Bugs / Calibration Notes

Patterns that look wrong out of context but are accepted PyPy idiom. Every
entry here was found by actually running a scanner against the real
`pypy/pypy` checkout and investigating a surprising result — not predicted in
advance. Add to this file whenever a scanner's false-positive rate on real
code reveals a pattern its logic doesn't yet know about.

## `we_are_translated()` divergence

**Single-arm debug-only guards.** `if not we_are_translated(): assert ...` —
a sanity check that only runs under the untranslated test-suite mode.
Mechanically recognized by `scan_translated_divergence.py`'s
`is_debug_only_body`. Real coverage is lower than first assumed: an early
version of this toolkit's design doc claimed this was "the dominant shape...
every sampled instance," generalizing from ~5-8 hand-picked examples that
happened to come disproportionately from files with "debug" in the name
(`rpython/rlib/debug.py`, `rpython/rtyper/debug.py`). Running the actual
scanner against all 226 real single-arm sites found only 15% match the flat
shape. Two more shapes were found and added:
- Asserts wrapped in a simple `for`/`if` (e.g.
  `rpython/memory/gc/minimarkpage.py`: `if not we_are_translated(): for a in
  self._all_arenas(): assert a.nfreepages == 0`).
- The early-return idiom: `if we_are_translated(): return` immediately
  preceding an assertion the translated build skips
  (`pypy/interpreter/pyframe.py`'s `assert_stack_index`).

Even combined, only ~22% of real single-arm sites are recognizably
debug-adjacent. **The remaining ~78% are not false positives to suppress —
they're real findings needing per-site attention**, just not yet
characterized into a recognizable non-bug shape.

**Deliberate emulation shims (two-arm sites).**
`rpython/rlib/rgil.py` maintains a whole parallel `EmulatedGilHolder` class
so GIL logic can be exercised under the untranslated test suite before the
real translated GIL primitives exist. Both arms call different, real,
substantive implementations. Classified POLICY, not FIX or CONSIDER — this
is deliberate architecture, not an oversight, even though it's a genuine
behavioral divergence between execution modes.

## Immutability contracts

**Quasi-immutable list markers: `?` comes before `[*]`, not after.**
`'closure?[*]'`, not `'closure[*]?'`. A marker check that only looks at the
string's tail (`raw.endswith('?')`) will wrongly classify these as claiming
full immutability. Confirmed against `pypy/interpreter/function.py`'s
`Function.closure`/`Function.defs_w`. The corrected parser lives in
`pypy_utils.classify_immutable_field` — checks for `?` anywhere in the raw
string, not just the tail.

**`jit.promote()` of an unrelated attribute is not evidence of anything.**
`GeneratorIterator` calls `jit.promote(frame.last_instr)` — a real promote
call, but on a completely different object's attribute, not `self`. An
earlier version of `scan_immutability_contracts.py` matched any call to
something named `promote` regardless of argument, which wrongly tiered this
class's finding as "strongest evidence" alongside the genuine `CPPMethod`
case. The check now requires the promoted argument be literally `self`.

**Cleanup-time reassignment inside `__del__` is lower priority, not
necessarily wrong.** `W__StructInstance.rawmem` is nulled in `__del__` after
being set in `__init__` — a defensible defensive-cleanup pattern, since a
finalizing object generally isn't still being actively JIT-traced. Tiered
CONSIDER/low, not treated the same as a mid-lifetime reassignment on a live
object.

## Interp/app boundary

**A raw exception with a docstring acknowledging it is likely deliberate.**
`pypy/interpreter/argument.py`'s `fixedunpack`: "get the 'argcount'
arguments, or raise a **real** ValueError if the length is wrong." Traced
every caller in the non-test tree: none exist. The only call site is its own
test, which explicitly asserts the `ValueError`. Resolved ACCEPTABLE with
hard evidence (zero production reachability), not just the docstring alone —
the caller-reachability check in `scan_interp_app_boundary.py`'s `analyze()`
does the cheap version of this same trace for every isolated raw-exception
finding, but it's a text-based heuristic (`funcname(` occurrences), not real
call-graph analysis, and should be spot-checked rather than trusted blindly.

## RPython restrictions

**`eval`/`exec` inside a function body is very likely the recognized codegen
idiom, not a real violation.** All 21 real inside-function candidates traced
individually during investigation turned out to be one of two benign
patterns:
1. Generate a specialized method/function once via `exec()` at class-body or
   module-load time, before the annotator ever sees the result
   (`rpython/jit/metainterp/pyjitpl.py` stamps out opcode-handler methods
   this way; `rpython/memory/gctransform/refcounting.py` generates
   specialized deallocators the same way, itself inside the GC-transform
   translation pass).
2. Build/translation-tooling reading a spec string
   (`rpython/config/parse.py`'s config-value parser;
   `rpython/jit/codewriter/support.py`'s `oopspec` string parser at
   codewriter-generation time) — never runs as translated interpreter code.

Zero of the 21 were genuine violations. `scan_rpython_restrictions.py`
deliberately does not check `eval`/`exec` at all rather than ship a check
that would be pure noise on the real checkout.

## Sibling guard consistency

**A `close()`/`close_w()` method legitimately doesn't need the class's own
"is this closed" guard.** `pypy/module/_io/interp_bytesio.py`'s
`W_BytesIO.close_w()` calls `self.close()` directly with no
`self._check_closed(...)` call, unlike 13 sibling exposed methods on the
same class. This isn't a missing guard — being closed is exactly the state
it's fine to be in when calling `close()` again (idempotent close is a
normal, expected pattern), so requiring the guard here would be wrong, not
just redundant. `scan_sibling_guard_consistency.py` can't distinguish "this
method's whole job is to transition into the guarded state" from "this
method forgot to check the guarded state" — worth an agent's judgment call
whenever the unguarded method's name is `close`/`close_w` or similarly
state-transitioning.

**A method delegating to an already-guarded sibling doesn't need its own
guard call.** Same file, `read1_w()`'s entire body is
`return self.read_w(space, w_size)` — `read_w` itself calls
`self._check_closed(space)`, so `read1_w` is genuinely, correctly
protected, just indirectly. The scanner only checks whether a method calls
the guard *directly*, not whether it delegates to another method that does
— the same "guard via an intermediate call, not the immediate function"
limitation `scan_unvalidated_helper_calls.py` already has for its own
guard-hint check. Real call-graph analysis would resolve this properly;
out of scope for a fast static scanner, so this needs a human/agent
judgment call whenever an "unguarded" finding's body is a one-line
delegation to another method on the same class.

**Abstract base classes need a proportion threshold, not just an absolute
count.** `W_IOBase` (the base class for all `_io` types) has ~20 exposed
methods, only 5 of which call a guard — the other ~15 are no-op/stub
defaults meant to be overridden by concrete subclasses, not a class with an
established convention one method deviates from. An earlier version of
this scanner's "at least 2 methods use a guard" threshold flagged all ~15
as "missing the convention," which was wrong for all of them at once.
Fixed by requiring a majority (≥50%) of a class's evaluated exposed methods
to be guarded before treating the pattern as an established convention
worth deviating from.

## Unvalidated RPython helper calls

**`ord(s[0])` passed to `unichr_as_utf8` is safe by construction, not by a
visible guard.** `pypy/objspace/std/newformat.py`'s nested `_lit()` function
calls `rutf8.unichr_as_utf8(ord(s[0]))` with no comparison-based guard
anywhere in its scope — `scan_unvalidated_helper_calls.py` correctly flags
it CONSIDER, but the actual reason it's safe isn't a missing-but-implied
check, it's that `ord()` of a single character from an already-valid Python
`str` object can never exceed `0x10FFFF` by the type's own invariant. The
scanner has no way to see a type-level guarantee like this — worth an
agent's judgment call rather than a scanner-side fix, since recognizing
"this value came from `ord()` of a str-indexing expression" as inherently
safe would need real data-flow typing, not a textual guard-hint check.

**A lookup-table-derived codepoint can still be a real (lower-severity)
finding.** `pypy/module/unicodedata/interp_ucd.py`'s `lookup()` method calls
`unichr_as_utf8(code)` after `assert code >= 0` but with **no upper-bound
check** against `0x10FFFF` anywhere in the function. `code` comes from
`self._lookup(name.upper())` — a Unicode-name-to-codepoint table lookup, not
raw attacker-supplied bytes the way `struct.unpack('u', ...)`'s buggy path
is. Real finding, correctly flagged CONSIDER, but the practical risk is much
lower than the confirmed bug since exploiting it would require an
out-of-range entry in PyPy's own generated Unicode name table, not
arbitrary user input — worth noting the *shape* is identical to a confirmed
bug even though the *reachability* is very different, exactly the kind of
distinction the scanner's own CONSIDER classification exists to defer to a
human/agent judgment call.
