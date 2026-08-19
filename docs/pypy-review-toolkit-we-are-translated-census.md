# pypy-review-toolkit — `we_are_translated()` Census

*A second round of investigation while the flagship stays a "working
hypothesis" per danzin. Not asking "is this the right flagship" again —
asking "if it is, what does the real data say the scanner actually needs
to check." Ran against the real checkout; every example below is a real
line, not constructed.*

## 1. The surface is real and substantial

`grep`: **166 files** mention `we_are_translated`, **607 textual
occurrences**. AST walk for actual `if we_are_translated():` /
`if not we_are_translated():` branch statements (not just the plain
mentions — assignments, docstrings, etc. don't count): **284 real branch
sites**, found across the 79.6% of files `ast.parse()` handles directly
(the trial doc already showed the other 17.4% need the tree-sitter
fallback — this census undercounts by however many more sites live in
those 443 files, not yet run).

## 2. The two-arm / single-arm split changes the scanner's actual job

Of the 284 sites: **59 (21%) have an `else`/`elif` arm** — the shape §3.3
of the design doc describes, "diff both arms." **225 (79%) are a bare
`if`, no else at all.** That's the opposite of what the design doc's
phrasing implicitly assumed (it talks about "diffing both arms" as the
core mechanism without flagging that 4 in 5 real sites don't have two arms
to diff). Both patterns turned out to be real and worth handling, but
they're different problems:

### The 79% majority: `if not we_are_translated(): assert ...`

Every sampled single-arm site is the same shape — a sanity check that only
runs in the untranslated (test-suite) mode:

```python
# rpython/rlib/rutf8.py:782
if not we_are_translated():
    assert len(utf8[start: end].decode("utf-8")) == slicelength

# rpython/rlib/debug.py:481
def check_list_of_chars(l):
    if not we_are_translated():
        assert isinstance(l, list)
        for x in l:
            assert isinstance(x, (unicode, str)) and len(x) == 1
    return l

# rpython/rtyper/debug.py:51
def fatalerror(msg):
    if not we_are_translated():
        raise FatalError(msg)
    from rpython.rtyper.lltypesystem import lltype
    ...
    llop.debug_print_traceback(lltype.Void)
```

This is exactly the "acceptable divergence: debug-only instrumentation"
carve-out the design doc already names in §5's classification notes — but
it turns out to be *the dominant real shape*, not a footnote exception.
Good news for precision: it's mechanically recognizable (a body consisting
only of `assert`/`raise` statements, with no assignment or call that
produces a value used later) and should be an automatic default-suppress
rule in the scanner itself, not something the agent has to reason about
case-by-case on 225+ sites. Bad news if we hadn't checked: without this
rule, the flagship agent would spend nearly all its attention on the
least-interesting 79% of its own target surface.

### The 21% minority: real two-arm sites, often deliberate alternate implementations

```python
# rpython/rlib/rgil.py:163
def release():
    if we_are_translated():
        _gil_release()
    else:
        allocate()
        _emulated_gil_holder.release()

# rpython/rlib/rgil.py:172
def acquire():
    if we_are_translated():
        from rpython.rlib import rthread
        _gil_acquire()
        rthread.gc_thread_run()
    else:
        allocate()
        _emulated_gil_holder.acquire()
```

This is the pattern the design doc's flagship description actually had in
mind — but the real examples show it's usually **not an accident to catch,
it's a deliberate emulation shim.** `rpython/rlib/rgil.py` maintains a
whole parallel `EmulatedGilHolder` implementation specifically so GIL
logic can be exercised under the untranslated test suite without the real
translated GIL primitives existing yet. That's real, substantial,
by-design divergence — correctly POLICY under §5's classification, not
FIX or even CONSIDER, and the design doc's framing of the intentional case
as "debug-only instrumentation" underdescribes what this actually looks
like in practice. Worth naming this as its own recognizable sub-pattern
("parallel emulation shim, both arms produce/consume real values, call
different named implementations") rather than folding it into the same
bucket as bare debug asserts.

## 3. What this changes in the design

- **Scanner needs an automatic suppress rule for the single-arm
  `assert`/`raise`-only shape** — this is the majority case and it's
  mechanically detectable, not something worth spending agent judgment on
  225+ times per run.
- **§5's POLICY description should separate two recognizable intentional
  sub-patterns** rather than one: (a) single-arm debug/sanity-check-only
  (suppress by default, per above) and (b) two-arm deliberate alternate
  implementation / emulation shim (still surface as POLICY, since it's
  worth a maintainer's awareness, but shouldn't be conflated with "just a
  print statement" in the agent's framing).
- **This doesn't resolve whether the flagship is right** — that's still
  danzin's call, per his own answer. What it does is make sure that *if*
  it stays flagship, the scanner isn't naively diffing 284 sites' worth of
  arms and drowning in the 79% that are a known-benign shape. Worth having
  this regardless of the final flagship decision, since `rpython/rlib/debug.py`
  and its siblings will remain part of whatever gets reviewed either way.
- **Full census still incomplete** — 443 files (17.4% of the tree) aren't
  covered here because they need the tree-sitter fallback from the AST
  trial, not the plain `ast` walk this census used. Worth re-running once
  `pypy_utils.py`'s dual-parser dispatch exists, since files like
  `rpython/rlib/jit.py` (partial tree-sitter recovery, per the AST trial)
  could plausibly contain more sites of either shape.
