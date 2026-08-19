# Five PyPy 3.11 bugs found by fuzzing

**Target:** PyPy 7.3.23 / Python 3.11.15, linux x86_64 (`uv python install pypy-3.11`)
**Compared against:** CPython 3.14.3
**Found with:** [fusil](https://github.com/devdanzin/fusil), differential-fuzzing the stdlib
**Status:** none reported yet — this is the "is this worth your time?" pass

All five reproduce on a stock release build. Three are one-liners. Every case below was run on
both interpreters; the CPython column is what the same input does there.

---

## Summary

| # | Bug | Repro | PyPy | CPython |
|---|---|---|---|---|
| 1 | Parser assertion on `compile()` of bytes | `compile(b"0\x80", "<s>", "exec")` | **SIGABRT** | `SyntaxError` |
| 2 | `struct` `'u'` format, unvalidated codepoint | `struct.unpack("u", b"abcd")` | `SystemError` (RPython leak) | `struct.error` |
| 3 | Multibyte encoder `setstate()`, unvalidated sign | `codecs.getincrementalencoder("cp949")().setstate(-1)` | `SystemError` (RPython leak) | `OverflowError` |
| 4 | `_lzma` double free (`ffi.NONE` typo + unlocked free) | ~10 lines | **SIGABRT** | n/a (different impl) |
| 5 | `_dealloc_warn` on a detached `TextIOWrapper` | 3 lines | **SIGSEGV** | no such method |

Two recurring shapes, both of which a static checker could look for:

- **An RPython-level exception escaping to application level** (#2, #3), which PyPy itself
  reports as `SystemError: unexpected internal exception (please report a bug)`. In both cases
  a value that came straight from user input was passed into an RPython helper
  (`unichr_as_utf8`, `rbigint.tobytes`) without the range/sign check that helper requires.
- **A missing guard in a hand-written module** (#4, #5): a raw `free()` reachable twice, and a
  method that dereferences a buffer its siblings all check for.

---

## 1. `compile()` of two bytes aborts the interpreter

```python
compile(b"0\x80", "<s>", "exec")
```

```
RPython traceback:
  File "pypy_interpreter_pyparser.c", line 3756, in PegParser__parse
  File "pypy_interpreter_pyparser.c", line 16487, in generate_tokens
  File "pypy_interpreter_pyparser.c", line 25296, in _maybe_raise_number_error
  File "pypy_interpreter_pyparser.c", line 30295, in raise_invalid_unicode_char
Fatal RPython error: AssertionError
Aborted (core dumped)
```

CPython raises `SyntaxError`.

**Trigger:** source given as **bytes**, whose first token is a **number**, immediately followed
by a byte that is not valid UTF-8. Both halves are required:

```
compile(b"0\x80", ...)  -> ABORT        compile(b"a\x80", ...) -> SyntaxError   (not a number)
compile(b"7\xc1", ...)  -> ABORT        compile(b"0\xff", ...) -> SyntaxError   (byte does not qualify)
                                        compile(b"\x80",  ...) -> SyntaxError   (no number)
```

The number is what routes it through `_maybe_raise_number_error`, which calls
`raise_invalid_unicode_char`, where the assertion fires. Reaches `compile()`, `exec()`, `eval()`
and `bytearray` sources, so **any program that compiles or execs untrusted bytes can be aborted
by a 2-byte input**, uncatchably.

**Prior art:** this is a recurring class in the PEG parser — pypy#4002, and pypy#5076 (trailing
backslash, fixed in #5708; that trigger no longer crashes on 7.3.23). This trigger goes through
the *number* path, which those did not.

`~/crashers/pypy_compile_bytes_abort/repro.py`

---

## 2. `struct.unpack("u", ...)` leaks an RPython exception

```python
import struct
struct.unpack("u", b"abcd")
```

```
PyPy:    SystemError: unexpected internal exception (please report a bug): <OutOfRange object ...>
           pypy_module_struct.c: UnpackFormatIterator_append_utf8  ->  rpython_rlib.c: unichr_as_utf8
CPython: struct.error: bad char in struct format
```

CPython **removed** the legacy `'u'` (UCS-4 character) format in 3.9. PyPy still accepts it,
reads four bytes as a codepoint, and passes it to `unichr_as_utf8` without validating it:

```
U+10FFFF   -> ok, ('\U0010ffff',)     the last valid codepoint
U+110000   -> leak                    first value above the range
U+FFFFFFFF -> leak
U+D800     -> leak                    surrogates are rejected by unichr_as_utf8 too
```

`b"abcd"` is 0x64636261 little-endian = 1684234849, far above U+10FFFF.

No `except struct.error` will catch this, and the message cannot say what actually went wrong.

**Related class, different site:** pypy#3047 ("Internal exception when loading json with unicode
escapes"). No prior report for the struct path.

`~/crashers/pypy_struct_u_rpython_leak/repro.py`

---

## 3. Multibyte encoder `setstate()` leaks an RPython exception

```python
import codecs
codecs.getincrementalencoder("cp949")().setstate(-1)
```

```
PyPy:    SystemError: unexpected internal exception (please report a bug): <InvalidSignednessError object ...>
           pypy_module__multibytecodec.c: MultibyteIncrementalEncoder_setstate_w  ->  rpython_rlib.c: rbigint_tobytes
CPython: OverflowError: can't convert negative int to unsigned
```

Any negative integer. The state is handed straight to `rbigint.tobytes()`, whose signedness
requirement is never checked.

**Scope:** all 12 multibyte *encoders* (`big5`, `cp932`, `cp949`, `euc_jp`, `euc_kr`, `gb18030`,
`gb2312`, `hz`, `iso2022_jp`, `johab`, `shift_jis`, `shift_jisx0213`) and the `StreamWriter` that
wraps one. The *decoders* take a tuple state and are unaffected. `rbigint.tobytes` is correctly
guarded elsewhere — `int.to_bytes()` raises `OverflowError` properly — so `setstate` looks like
the one unvalidated caller rather than a general `rbigint` problem.

**Not** pypy#5010, which was about *adding* `setstate`/`getstate`; this is a missing check inside
that implementation.

`~/crashers/pypy_multibytecodec_setstate/repro.py`

---

## 4. `_lzma` double free

```python
import lzma
from _lzma import ffi

blob = lzma.compress(bytes(range(256)) * 400)
d = lzma.LZMADecompressor()
for off in range(0, 200, 7):            # leave input pending -> allocates _input_buffer
    d.decompress(blob[off:off + 7], max_length=1)

d.lzs.avail_in = d._input_buffer_size + 1   # take the free branch
try:
    d.post_decompress_avail_data()
except AttributeError:
    pass                                # <- the buffer has already been freed here
d.clear_input_buffer()                  # frees the SAME pointer again
```

```
free(): double free detected in tcache 2
Aborted (core dumped)          # deterministic, 3/3
```

Two independent defects in `lib/pypy3.11/_lzma.py`:

**(a) A typo, line 562** — `post_decompress_avail_data`:

```python
if self._input_buffer is not ffi.NULL and self._input_buffer_size < lzs.avail_in:
    m.free(self._input_buffer)
    self._input_buffer = ffi.NONE          # ffi.NONE does not exist
```

`hasattr(ffi, "NONE")` is `False`. The free succeeds, then the assignment raises
`AttributeError`, leaving `self._input_buffer` pointing at freed memory. The very next statement
is `if self._input_buffer is ffi.NULL:` — the branch that would reallocate — which confirms
`ffi.NULL` was intended. Any later free of that stale pointer is a double free.

**(b) An unlocked check-then-act, line 575** — `clear_input_buffer`:

```python
def clear_input_buffer(self):
    if self._input_buffer is not ffi.NULL:
        m.free(self._input_buffer)
        self._input_buffer = ffi.NULL
```

Two threads both pass the NULL test and both free. The class *has* a `self.lock` and
`decompress()` uses it (`:595`), which is what makes the internal calls at `:618`/`:631` safe —
this exposed entry point simply does not take it. Reproduced separately with 8 threads:
`double free or corruption (!prev)`. **This is the form the fuzzer hit first.**

Both are reachable because PyPy exposes 14 names on `LZMADecompressor` where CPython exposes 5
(`_input_buffer`, `lzs`, `lock`, `clear_input_buffer`, `post_decompress_avail_data`, …).

**Related but distinct:** pypy#5330 (open) reports a memory *leak* in the same file and the same
buffer machinery.

`~/crashers/pypy_lzma_double_free/repro.py`

---

## 5. `_dealloc_warn` on a detached `TextIOWrapper` segfaults

```python
import io
w = io.TextIOWrapper(io.BytesIO(b"x"))
w.detach()
w._dealloc_warn(None)        # SIGSEGV
```

**This is an incomplete fix of pypy#5123**, not a new bug. #5123 was closed the next day with
"Fixed in 7bc330cce0f" — that commit added the guard to `pypy/module/_io/interp_bufferedio.py`,
checking `self.w_raw`, i.e. the **buffered** layer. The **text** layer, in `interp_textio.py`,
holds its buffer as `w_buffer` and was not touched. On 7.3.23:

```
BufferedReader  -> RuntimeError: calling _dealloc_warn' on a closed or detached reader
BufferedWriter  -> RuntimeError   (same)
BufferedRandom  -> RuntimeError   (same)
TextIOWrapper   -> SIGSEGV                              <-- still unguarded
```

The asymmetry inside `TextIOWrapper` itself shows the intended behaviour: on a detached wrapper
every *other* method raises cleanly, and only this one crashes.

```
read / write / flush / fileno / seek / close -> ValueError: underlying buffer has been detached
_dealloc_warn                                -> SIGSEGV
```

Needs one argument (value irrelevant); merely *closed* is handled correctly. CPython does not
expose `_dealloc_warn` on `TextIOWrapper` at all.

**Suggested fix:** mirror the 7bc330cce0f guard on `w_buffer`, or route it through the same
`_check_attached()` the sibling methods use.

`~/crashers/pypy_textio_dealloc_warn/repro.py`

---

## Questions for the PyPy maintainers

1. **Are the RPython-exception leaks (#2, #3) worth reporting individually?** They are not
   crashes — the program gets a `SystemError` it cannot meaningfully handle. The message says
   "please report a bug", which is why we are asking. If they are interesting, we can sweep for
   more: two independent sites turned up in one short fuzzing run, which suggests the class is
   not rare.
2. **Is the legacy `'u'` struct format (#2) meant to still be supported?** CPython removed it in
   3.9. If PyPy keeps it, it needs the range check; if not, rejecting it like CPython fixes the
   bug outright.
3. **Does the "private surface" argument reduce severity for #4 and #5?** Both need a method
   CPython does not expose (`clear_input_buffer`, `_dealloc_warn`). We think a double free and a
   segfault reachable from pure Python are worth fixing regardless, but you may weigh it
   differently. (`_dbm._dbm(b"x", 0, f).keys()` is a third instance we did **not** list above,
   because it is pypy#5115 — closed, but its fix only removed the class from `dbm.ndbm`'s
   namespace and the same segfault is still reachable via `_dbm`.)
4. **Would you like the not-yet-minimal ones?** We also have reliably reproducing but unminimized
   crashes on allocation-failure paths — SIGSEGV in `multiprocessing/pool.py`
   `_repopulate_pool_static` from a plain `ThreadPool()`, and heap corruption in an
   `importlib.resources` session — which only occur under an `RLIMIT_AS` cap. They are ~12k-line
   generated scripts, so they are only worth sending if that class interests you.

---

## Notes for a static-analysis toolkit

If you are seeding a checker with known bugs, these are the shapes worth encoding:

| Shape | Instances | What to look for |
|---|---|---|
| **User value → RPython helper without the helper's precondition** | #2 (`unichr_as_utf8`, needs a valid codepoint), #3 (`rbigint.tobytes`, needs non-negative) | calls into `rpython.rlib` helpers from `pypy/module/*` where the argument derives from an app-level object and no range/sign check precedes it. Both leak as `SystemError: unexpected internal exception`, so that string is a good dynamic oracle too. |
| **Raw `m.free()` / `lib.free()` in Python-level stdlib** | #4 (both defects) | `lib/pypy3.11/*.py` calling a C `free()`. Only three files do: `_lzma.py`, `_gdbm.py`, `_pypy_util_cffi.py`. Check each free is (a) followed by a NULL assignment that cannot raise, and (b) under the same lock as its siblings. |
| **Assignment to a non-existent attribute after a free** | #4a | `ffi.NONE`, `ffi.Null`, etc. A plain "does this attribute exist on the module/object" check would have caught it; it is invisible because it only executes on an error path. |
| **One method of a class missing the guard all its siblings have** | #5 | per-class: collect the methods that check a validity flag (`_check_attached`, `w_buffer is not None`, `self.closed`) and flag the ones that dereference the same field without it. |
| **Fix applied to one layer of a wrapper stack** | #5, and pypy#5115 | when a fix guards `w_raw` in `interp_bufferedio.py`, check whether the analogous `w_buffer` in `interp_textio.py` needs it. Both incomplete fixes we found are of exactly this shape. |
| **Namespace leakage of a pure-Python-over-cffi module** | enables #4, #5, #5115 | compare `dir(module)` against CPython's. `_lzma` exposes 14 names on `LZMADecompressor` vs CPython's 5; `_dbm` exposes its entire ctypes implementation. Everything extra is unguarded attack surface. |

A useful dynamic oracle for the whole first row: run any candidate input and grep stderr for
`unexpected internal exception (please report a bug)`. It is PyPy telling you it hit a bug, and
it costs nothing to check.
