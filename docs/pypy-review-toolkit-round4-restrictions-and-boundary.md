# Update for danzin — round 4 of investigation

Went deeper on two more of the open items from my last message: the
RPython-restriction scanner and the interp/app boundary checker. Same
discipline as before — real numbers off the actual checkout, not
predictions, and I checked my own heuristics against real code before
trusting the counts.

## 1. RPython-restriction scanner: `eval`/`exec` census

First pass, just grepping for `eval(`, gave 96 hits. That number is
wrong — almost all of it is `.eval()` method calls on unrelated objects
(`llframe.eval()`, `operation.eval(self)` in the flow-space machinery),
nothing to do with the builtin. Went back and did it properly with `ast`,
matching only `Call` nodes where `func` is a bare `Name` — i.e. the actual
builtin, not an attribute access. Real numbers, non-test code only:

- **8 real `eval()` calls**, **48 real `exec()` calls**.

Then I found a second, more interesting problem with the naive version of
this check: a flat "flag every eval/exec" scanner would still be wrong,
because most of these aren't restriction violations at all — they're a
recognized PyPy idiom for generating specialized RPython functions at
*translation setup time*, before the annotator ever sees them. Confirmed
by reading the actual code, e.g. `rpython/jit/metainterp/pyjitpl.py`:

```python
for _opimpl in [... a list of op names ...]:
    exec(py.code.Source('''
        @arguments("box", "box")
        def opimpl_%s(self, b1, b2):
            return self.execute(rop.%s, b1, b2)
    ''' % (_opimpl, _opimpl.upper())).compile())
```

That runs once, at class-body-execution time, to stamp out a family of
near-identical methods — the `exec()` itself disappears once the class is
built; only the generated `def`s (real, ordinary RPython methods) survive
into what actually gets translated. `rpython/rlib/rstruct/runpack.py` has
the same pattern with an even more direct signal — the comment right next
to it literally says `# override not-rpython version`.

Split the 48 real hits by whether the call sits at module/class top-level
(almost certainly this codegen pattern) vs. inside a function body
(needs a closer look): **35 at top-level, 21 inside a function.** But even
"inside a function" doesn't mean "real violation" — sampled two of the 21
(`runpack.py:106`, `pypy/module/micronumpy/loop.py:121`) and both turned
out to be the *same* codegen idiom, just wrapped in a factory function
that's itself called once at module-load time to produce a specialized
method (`call2_advance_out = generate_call2_cases(...)` — called once,
result assigned at module scope). Checked how many of the 21 use the
recognizable `py.code.Source(...).compile()` textual pattern nearby: 6 of
14 sampled matched a tight window check, but that's a lower bound — the
window was too narrow and missed at least one I'd already manually
confirmed uses the same idiom.

**What this means for the design:** a naive eval/exec scanner is nearly
useless here — it'd flag 48 sites and the overwhelming majority would be
false positives on a single, well-established PyPy idiom. The check needs
to specifically recognize "assigned-once code generator, called at
module/class scope, producing a method or function" as a suppressible
shape, the same way the `we_are_translated()` census showed the
single-arm-assert pattern needed its own suppress rule. I don't have a
clean count of the genuine remainder yet — that's honest work still to
do, not a number I want to guess at.

## 2. interp/app boundary checker: raw exceptions vs. `OperationError`

Censused `pypy/interpreter/` + `pypy/module/` (non-test) for `raise`
statements calling either `OperationError(...)` or a raw builtin
exception (`ValueError`, `TypeError`, etc.) directly by name:

- **107 `raise OperationError(...)` sites** (the correct convention)
- **97 raw-builtin-exception `raise` sites** — almost as many as the
  correct pattern, which is a bigger candidate pool than I expected

Breakdown of the 97: `NotImplementedError` (30), `TypeError` (27),
`ValueError` (23), `OSError` (4), `ImportError` (4), `Exception` (3),
the rest single digits.

Sampled one of the `ValueError` sites before assuming they're all bugs —
`pypy/interpreter/argument.py`, `fixedunpack`:

```python
def fixedunpack(self, argcount):
    """The simplest argument parsing: get the 'argcount' arguments,
    or raise a real ValueError if the length is wrong."""
    if self.keywords:
        raise ValueError("no keyword arguments expected")
    ...
```

The docstring itself says "raise a **real** ValueError" — that phrasing
reads like a deliberate distinction PyPy's own authors are drawing between
a genuine Python-level exception (meant to be caught internally by
whatever interp-level code called `fixedunpack`, and possibly converted to
an `OperationError` there) versus an app-level exception. This isn't
necessarily a bug at all — it might be entirely correct if every caller of
`fixedunpack` catches `ValueError` internally. Whether it's a real
boundary leak depends on whether any caller's own error handling is
scoped to catch `OperationError` specifically and would let a raw
`ValueError` slip through uncaught into the running Python program — which
needs actual call-graph reachability, not just a raise-site count.

**What this means for the design:** same shape as the eval/exec finding —
a static raise-site census gives a real, sizeable candidate pool (97,
nearly matching the 107 "correct" sites), but distinguishing "real bug,
this leaks to app-level" from "deliberate internal signaling, documented
as such, always caught" needs either a call-graph pass I haven't built
yet, or the agent doing real judgment per candidate the way the family's
own FIX/CONSIDER/POLICY discipline expects. I'd treat every one of these
97 as CONSIDER by default until reachability is checked, not FIX — the
`fixedunpack` example alone is reason enough not to assume the raw count
is the bug count.

## 3. Where this leaves things

Both censuses landed on the same lesson, which is worth naming plainly
since it's now shown up three times (the earlier `we_are_translated()`
single-arm pattern, now eval/exec codegen, now the `fixedunpack`
docstring): **a flat AST pattern-match is enough to find the candidate
surface, but PyPy's codebase has enough deliberate, documented,
self-aware idiom in exactly the places these checks look that none of the
four scanners I've now run (the two from last round, these two) can ship
without a suppress/recognize step built in from day one.** That's not a
reason not to build them — the real candidate pools are all substantial
(21 eval/exec, 97 raw-exception, 9 immutability, 59 two-arm
`we_are_translated()`) — but it does mean none of the v0.1 scanners should
be scoped as "just the AST check," the recognize-and-suppress logic is
part of the minimum viable version, not a v0.2 refinement.

Still haven't gotten to: individually inspecting the remaining 7 (of 9)
immutability candidates I haven't looked at yet, or extending any of these
censuses through the tree-sitter fallback to cover the 17.4% of files
`ast` can't parse directly — both real gaps in what I've checked so far,
happy to keep going on either.
