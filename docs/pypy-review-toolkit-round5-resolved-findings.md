# Update for danzin — round 5, resolving the open threads

Went back through the two things I'd flagged as "needs someone with real
call-graph knowledge" instead of leaving them as open questions — traced
actual callers for both. Also caught a real bug in my own immutability
census script before it went further, which changes the numbers I sent
last round. Reporting that honestly rather than letting a wrong count
stand.

## 1. Correction to last round's crown-jewel count: 9 → 7

Rechecking the `Function` class candidate (`closure`, `defs_w`) by hand, I
found my own marker-detection logic was wrong. PyPy's actual convention
for a quasi-immutable list field is `'closure?[*]'` — the `?` comes
**before** `[*]`, not after. My script's check only looked for `?` at the
very end of the string or the substring `'[*]?'`, so it missed the
`'name?[*]'` ordering entirely and mis-flagged `Function.closure` and
`Function.defs_w` as "claims full immutability" when they're actually
already correctly marked quasi-immutable. Fixed the check to just look for
`?` anywhere in the declared entry, re-ran the whole census:

**7 real candidates, not 9** (was: `pypy/module/__builtin__/interp_classobj.py`'s
`W_ClassObject.bases_w` and `pypy/interpreter/function.py`'s `Function`
also drop out on closer inspection — same root cause). The corrected list:

- `pypy/module/_rawffi/alt/interp_struct.py` — `W__StructInstance.rawmem`
- `pypy/module/_cppyy/interp_cppyy.py` — `CPPMethod` (4 fields)
- `pypy/module/_cffi_backend/realize_c_type.py` — `W_RawFuncType` (3 fields)
- `pypy/module/struct/interp_struct.py` — `W_Struct.format`/`.size`
- `pypy/interpreter/pycode.py` — `PyCode.co_filename` (the one I already
  flagged as the strongest example)
- `pypy/interpreter/generator.py` — `GeneratorIterator.pycode` (new, see
  below)
- `pypy/objspace/std/mapdict.py` — `UnboxedPlainAttribute` (2 fields)

Went through the two I hadn't looked at individually yet:

**`GeneratorIterator.pycode`** — declared `_immutable_fields_ = ['pycode']`,
no `?`, set once in `__init__`. Traced the second assignment: it's inside
`descr__setstate__`, PyPy's generator-unpickling restore path. A restored
generator gets its `pycode` reassigned after the object already exists —
same structural shape as `co_filename`, different lifecycle trigger
(pickle restore instead of a translation-time freeze step).

**`W__StructInstance.rawmem`** — traced all three assignments by hand:
two are inside `__init__` (branches for the allocate-vs-borrow-memory
case, both fine), the third is inside `__del__`. This is a different risk
shape than the other two — it's a cleanup-time reassignment (nulling out
a raw pointer on finalization), not a mid-lifetime one on a still-live
object. Worth noting as its own category: a finalizer reassignment is a
much more defensible pattern than reassigning a field on an object that's
still being actively used, and I don't think it deserves the same
priority as `co_filename`/`GeneratorIterator.pycode`/the `cif_descr`
finding from last round.

**Net effect on the crown jewel's real candidate rate:** 6 of 7 (excluding
the `__del__` case) are genuine mid-lifetime reassignments on a field
declared fully immutable, out of 201 classes checked — a workable signal
rate, and now I've actually looked at every one of them instead of just
the strongest sample.

## 2. Resolved: `fixedunpack`'s raw `ValueError` is not a bug

Traced every caller of `pypy/interpreter/argument.py`'s `Arguments.fixedunpack`
across the non-test tree: **there are none.** The only place it's called
at all is its own test file, `pypy/interpreter/test/test_argument.py`,
which explicitly asserts the `ValueError` gets raised:

```python
py.test.raises(ValueError, args.fixedunpack, 1)
```

So the docstring's "raise a real ValueError" wording wasn't just a hint —
it's tested, deliberate, documented behavior with zero production call
sites in this checkout. This resolves cleanly to ACCEPTABLE under the
family's classification scheme, with actual evidence behind it rather
than my earlier "needs judgment" hedge. Good validation that the
discipline works — I'd rather report a candidate that turned out clean
than only report the ones that look bad.

## 3. New, stronger candidate found while checking the above: `pyframe.py:236`

While I was in the habit of tracing callers, I went back to the raw
`ValueError` census from last round and looked at a second site instead
of stopping at one resolved example. `pypy/interpreter/pyframe.py`, inside
`initialize_frame_scopes`:

```python
if outer_func and outer_func.closure:
    ...
raise oefmt(space.w_TypeError,
            "directly executed code object may not contain free "
            "variables")
...
if closure_size != nfreevars:
    raise ValueError("code object received a closure with "
                         "an unexpected number of free variables")
```

Same function, two structurally similar internal-consistency checks, and
they use **different conventions** — the first correctly goes through
`oefmt`/`OperationError`, the second raises a raw `ValueError` a few lines
later. That asymmetry inside one function is a much stronger signal than
a raise site in isolation.

Traced the caller: `initialize_frame_scopes` is called from
`PyFrame.__init__` — meaning this runs on **every frame construction**,
one of the hottest paths in the whole interpreter. I don't have a way to
confirm from static reading alone whether a code object with a genuinely
mismatched closure/freevars count can actually reach this path from
app-level code (that would need knowing what validates code objects
before they get here — normal `compile()`, `marshal` loading a `.pyc`,
etc.) — but the asymmetry with the correct pattern three lines above,
combined with how central the call site is, makes this the strongest raw-exception
candidate I've found, stronger than anything in last round's raw list of
97. Flagging this one specifically rather than the general count.

## 4. Where this leaves the two open scanners

- **Crown jewel (immutability contracts):** now fully accounted for — all
  7 corrected candidates individually traced, not just sampled. 6 are
  genuine mid-lifetime reassignments, 1 (`rawmem`) is a lower-priority
  finalizer case. Confident this check works as designed once the
  quasi-immutable marker detection is right (which took me two tries to
  get right myself, which is itself useful — worth building a few
  `'field?[*]'`-shaped test fixtures early, since the ordering is easy to
  get wrong).
- **Interp/app boundary:** `fixedunpack` resolved clean with hard evidence
  (zero production callers). `pyframe.py:236` is now the strongest
  concrete candidate on the list, specifically because of the
  in-function asymmetry with the correct pattern, not just because it's a
  raw exception. I'd suggest this scanner's design lean on that
  same-function-asymmetry signal generally — it's a much better
  discriminator than a flat raise-site count, and it's genuinely checkable
  with a scanner rather than needing call-graph work every time.

Still haven't touched: the RPython-restriction eval/exec remainder (I
left 21 candidates only partially characterized last round), or extending
any of these censuses through the tree-sitter fallback for the 17.4% of
files `ast` can't reach. Let me know if you want me to keep pushing on
either before you've had a chance to look at what's here — happy to keep
going, just didn't want to keep piling on rounds without checking in.
