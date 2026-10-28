"""Goals (task.json) and per-issue notes for the fresh series.

Each goal = the upstream issue / PR description rewritten as a user report (root cause and
the fix itself removed) plus the concrete behaviours the hidden tests pin (exact outputs,
names of new public/private hooks the tests import), so a correct fix is gradable.
"""

GOALS = {}
NOTES = {}


def G(name, why, goal, note=None):
    GOALS[name] = goal
    NOTES[name] = {"why": why}
    if note:
        NOTES[name]["goal_note"] = note


# ─────────────────────────────────────────────────────────────────────────────
G("fresh_sqlglot_8335",
  "MySQL dialect parser rework: index key parts must distinguish `col(length)` prefixes from "
  "functional `(expr)` parts on three DDL paths (INDEX/KEY, UNIQUE, PRIMARY KEY) and UNIQUE must "
  "accept `USING <type>` before the column list; several validate_identity round-trips pin exact output.",
  r'''MySQL: index prefixes and `USING BTREE` are not handled consistently in DDL

In the MySQL dialect, index column prefixes like `c(20)` are only parsed correctly on the `PRIMARY KEY`
path. On the `INDEX`/`KEY` and `UNIQUE` paths the prefix is mangled (e.g. `INDEX k (c(10))` comes back as
`INDEX k (C(10))`, i.e. treated as a function call), and `UNIQUE ... USING BTREE (cols)` (index type written
before the column list) fails or round-trips wrongly.

MySQL's grammar is `key_part: {col_name [(length)] | (expr)} [ASC | DESC]` for every index kind, and
`UNIQUE [INDEX | KEY] [index_name] [index_type] (key_part,...) [index_option] ...`.

Expected round-trips with `read="mysql"`/`write="mysql"` (input -> output):

- `CREATE TABLE t (c VARCHAR(64), PRIMARY KEY (c(20)))` -> unchanged
- `CREATE TABLE t (d INT, INDEX k ((d + 1)))` -> unchanged (functional key part stays an expression)
- `CREATE TABLE t (c VARCHAR(64), KEY k (c(20)))` -> `CREATE TABLE t (c VARCHAR(64), INDEX k (c(20)))`
- `CREATE TABLE t (c VARCHAR(64), UNIQUE KEY u (c(20) DESC))` -> `CREATE TABLE t (c VARCHAR(64), UNIQUE u (c(20) DESC))`
- `ALTER TABLE t ADD INDEX k (c(20))` -> unchanged
- `ALTER TABLE t ADD PRIMARY KEY (c(20))` -> unchanged
- `ALTER TABLE t ADD UNIQUE KEY u (c(20))` -> `ALTER TABLE t ADD UNIQUE u (c(20))`
- `ALTER TABLE t ADD UNIQUE KEY u USING BTREE (c)` -> `ALTER TABLE t ADD UNIQUE u (c) USING BTREE`
- `CREATE TABLE t (a INT, UNIQUE KEY u USING BTREE (a))` -> `CREATE TABLE t (a INT, UNIQUE u (a) USING BTREE)`
- `CREATE TABLE t (a INT, UNIQUE USING BTREE (a))` -> `CREATE TABLE t (a INT, UNIQUE (a) USING BTREE)`
- `CREATE TABLE t (a INT, UNIQUE KEY `using` (a) USING BTREE)` -> `CREATE TABLE t (a INT, UNIQUE `using` (a) USING BTREE)`

Existing MySQL round-trips (e.g. `ALTER TABLE t ADD UNIQUE KEY uq (a) USING BTREE COMMENT 'why' INVISIBLE` ->
`ALTER TABLE t ADD UNIQUE uq (a) USING BTREE COMMENT 'why' INVISIBLE`, `UNIQUE KEY d (a) KEY_BLOCK_SIZE=8` ->
`UNIQUE d (a) KEY_BLOCK_SIZE = 8`) must keep working.''')

G("fresh_sqlglot_8574",
  "Three-file change across the expression model, parser and generator: new optional AST args "
  "(`With.cycle`, `RecursiveWithSearch.to/default`), a lookahead so a CYCLE clause can follow a SEARCH "
  "clause, and generator output for all of it in Oracle and Postgres.",
  r'''Recursive CTE: SEARCH followed by CYCLE, and CYCLE ... TO ... DEFAULT, fail to parse

In a recursive CTE, a `SEARCH` clause followed by a `CYCLE` clause fails to parse, and so does a `CYCLE`
clause with `TO ... DEFAULT`. Oracle and Postgres both accept these.

```python
import sqlglot
cte = "WITH t(id, mgr) AS (SELECT 1, NULL FROM dual UNION ALL SELECT e.id, e.mgr FROM t r, emp e WHERE r.id = e.mgr)"
sqlglot.parse_one(f"{cte} SEARCH DEPTH FIRST BY id SET ord CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM t", read="oracle")
# ParseError: alias does not support CTE
sqlglot.parse_one(f"{cte} CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' SELECT * FROM t", read="oracle")
# ParseError: column does not support CTE
```

Expected:
- Both statements parse and generate back identically (Oracle and Postgres), e.g. for Postgres
  `WITH RECURSIVE search_graph(id, link, data, depth) AS (...) CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path SELECT * FROM search_graph`
  and `... SEARCH DEPTH FIRST BY id SET ordercol CYCLE id SET is_cycle USING path SELECT * FROM search_graph`
  round-trip unchanged. The generated order is `CYCLE <col> SET <col> [TO <value> DEFAULT <value>] [USING <col>]`.
- AST shape: `exp.RecursiveWithSearch` gets two new optional args, `to` and `default` (the parsed values,
  e.g. `exp.Literal.string("Y")` / `exp.Literal.string("N")`). `exp.With` gets a new optional arg `cycle`
  holding a CYCLE clause that follows a SEARCH clause; the SEARCH clause stays in `search`. A lone clause
  (only SEARCH or only CYCLE) still goes into `search`. So for `WITH ... SEARCH DEPTH FIRST BY hire_date SET order1
  CYCLE hire_date SET is_cycle TO 'Y' DEFAULT 'N' SELECT ...`, `with_.args["search"].args["kind"] == "DEPTH"`
  and `with_.args["cycle"].args["kind"] == "CYCLE"` with `to`/`default` set as above.

Column lists in CYCLE (`CYCLE a, b ...`) are out of scope.''')

G("fresh_sqlglot_8538",
  "Touches the shared type-literal path in the base parser (ordering of zone-aware rewriting vs "
  "dialect literal parsers) plus a new Trino literal parser that infers precision from the literal text.",
  r'''Trino: TIMESTAMP / TIME literals lose their sub-second precision when transpiled

Trino writes timestamp literals as `TIMESTAMP '<str>'`. sqlglot parses these into a cast of the string to
the type, but drops the literal's precision, so the meaning changes on the way back: an unadorned `TIMESTAMP`
in Trino is `TIMESTAMP(3)`, which truncates the value.

```python
import sqlglot
sqlglot.transpile("SELECT TIMESTAMP '2026-03-01 00:00:00.1234'", read="trino", write="trino")[0]
# 'SELECT CAST(\'2026-03-01 00:00:00.1234\' AS TIMESTAMP)'   <- precision lost
```

Expected (read and write Trino), when the literal has more than 3 fractional-second digits the target type
carries that precision; literals with up to 3 digits stay unadorned:

- `SELECT TIMESTAMP '2026-03-01 00:00:00.1234'` -> `SELECT CAST('2026-03-01 00:00:00.1234' AS TIMESTAMP(4))`
- `SELECT TIMESTAMP '2026-03-01 00:00:00.123456789'` -> `SELECT CAST('2026-03-01 00:00:00.123456789' AS TIMESTAMP(9))`
- `SELECT TIMESTAMP '2026-03-01 00:00:00.1234 +02:00'` -> `SELECT CAST('2026-03-01 00:00:00.1234 +02:00' AS TIMESTAMP(4) WITH TIME ZONE)`
- `SELECT TIME '01:02:03.1234'` -> `SELECT CAST('01:02:03.1234' AS TIME(4))`

Existing behaviour stays: `SELECT TIMESTAMP '2012-10-31 01:00 +2'` -> `SELECT CAST('2012-10-31 01:00 +2' AS TIMESTAMP WITH TIME ZONE)`,
`SELECT TIME '01:02:03.456 -08:00'` keeps its current output, and other dialects are unaffected.''')

# ─────────────────────────────────────────────────────────────────────────────
G("fresh_attrs_1592",
  "Feature across four modules of attrs' code generator (`_make`, `_next_gen`, `setters`, `_compat`): "
  "generator-function hooks need two-phase execution around the actual attribute set, cached "
  "generator detection, and exact error semantics before/after the yield and on a second yield.",
  r'''Allow `on_setattr` hooks to be generator functions (run code after the value is set)

`on_setattr` hooks today can only transform the value before it is set. It would be useful to also run code
*after* the attribute has been set (e.g. notify observers with the new state). Proposal: an `on_setattr` hook
may be a generator function. It receives `(instance, attribute, value)` like a regular hook, and the value it
yields is what gets set; code after the `yield` runs once the attribute has been set on the instance.

Required behaviour (for `attr.s`/`attr.ib`, `attr.define`/`attr.field`, per-attribute and class-level
`on_setattr`, and `make_class`):

- `def hook(inst, attrib, value): yield "yielded!"` -> after `h.x = "xxx"`, `h.x == "yielded!"`.
- Code before the yield sees the old value and code after the yield sees the new value: a hook doing
  `calls.append(("pre", inst.x)); yield value; calls.append(("post", inst.x))` records
  `[("pre", "x"), ("post", "xxx")]` for `h = Hooked("x"); h.x = "xxx"`.
- Like regular hooks, generator hooks are NOT run during `__init__`.
- With `attr.define`, `x: str = attr.field(on_setattr=hook)` where the hook yields `value.upper()` -> `c.x = "world"` gives `"WORLD"`.
- A class-level generator hook runs once per assignment.
- If the generator raises before yielding, the exception propagates and the attribute keeps its old value.
- If it raises after yielding, the value is already set and the exception propagates.
- If it yields more than once: raise `RuntimeError` whose message contains
  "Generator on_setattr hook yielded more than once"; the first yielded value stays set and the generator is
  closed (its `finally` runs).

Generator detection must be cheap: add to `attr._compat` a function `_lazy_is_generator(func)` returning a
zero-argument callable that reports whether `func` is a generator function, caching the result in a module-level
dict `_IS_GENERATOR_RESULTS` keyed by the function (`_IS_GENERATOR_RESULTS[gen] is True` / `is False` after the
call). If the object is not hashable, the result is simply not cached but is still correct
(`_lazy_is_generator({})()` is falsy). Update the type stubs accordingly.''')

G("fresh_boltons_441",
  "Timezone arithmetic per PEP 495: fold-aware `dst()` for both the repeated and the missing hour, "
  "corrected transition constants, and a custom `fromutc()`; easy to get one direction right and the other wrong.",
  r'''`boltons.timeutils.USTimeZone` (e.g. `Eastern`) ignores `datetime.fold` at the fall-back transition

Ambiguous US/Eastern wall times at the 2011 fall DST transition ignore `fold`:

```python
from datetime import datetime, timezone
from boltons.timeutils import Eastern

unfolded = datetime(2011, 11, 6, 1, 30, tzinfo=Eastern, fold=0)
folded = datetime(2011, 11, 6, 1, 30, tzinfo=Eastern, fold=1)
print(unfolded.utcoffset(), unfolded.tzname())  # -05:00 EST, should be -04:00 EDT
print(folded.utcoffset(), folded.tzname())      # -05:00 EST
```

Converting from UTC has the same problem: both `2011-11-06T05:30Z` and `2011-11-06T06:30Z` map to a local
`01:30` with `fold=0`, so two distinct instants become indistinguishable.

Expected (PEP 495 semantics):
- `unfolded` (fold=0): `utcoffset() == -4h`, `tzname() == "EDT"`; `folded` (fold=1): `-5h`, `"EST"`.
- `datetime(2011, 11, 6, 5, 30, tzinfo=timezone.utc).astimezone(Eastern)` is local `2011-11-06 01:30`, `fold == 0`, `EDT`;
  `datetime(2011, 11, 6, 6, 30, tzinfo=timezone.utc).astimezone(Eastern)` is local `2011-11-06 01:30`, `fold == 1`, `EST`.
- The spring-forward gap should follow PEP 495 as well. The US rule is: DST ends at 2am (local daylight time
  becomes 1am standard time) on the first Sunday of November. Stay dependency-free; read `fold` with
  `getattr(dt, "fold", 0)`. `LocalTZInfo` is out of scope.''')

G("fresh_boltons_513",
  "Two independent stream bugs in `ioutils` (write return values / buffer-protocol inputs with byte-accurate "
  "rollover, and character-relative backwards seeks in a UTF-8 spooled text stream), each covering rolled and in-memory states.",
  r'''`boltons.ioutils` spooled streams: write() returns None, BufferedWriter breaks, backwards text seeks are wrong

Several problems with `SpooledBytesIO` / `SpooledStringIO`:

1. `write()` returns `None` instead of the number of bytes / characters written (both before and after
   `rollover()`). `SpooledBytesIO().write(b"abc")` should return `3`, `write(b"")` -> `0`;
   `SpooledStringIO().write("a☃b")` should return `3` (characters, not bytes).
2. Wrapping a `SpooledBytesIO` in `io.BufferedWriter` fails on `flush()`, because BufferedWriter passes a
   `memoryview`. Any buffer-protocol object should be accepted: `writer = io.BufferedWriter(stream);
   writer.write(b"payload"); writer.flush()` -> `stream.getvalue() == b"payload"` (rolled or not).
3. The rollover decision must count bytes of the buffer, not items: with `SpooledBytesIO(max_size=4)`, writing
   `memoryview(bytearray(b"abcd")).cast("I")` (one 4-byte item) returns `4`, triggers rollover (`stream._rolled`
   is True) and `getvalue() == b"abcd"`.
4. `SpooledStringIO` backwards relative seeks are wrong: after `stream.write("a☃bc")` and `stream.seek(3)`,
   `stream.seek(-2, os.SEEK_CUR)` must return `1`, then `stream.read(2) == "☃b"` and `stream.tell() == 3`
   (currently it consumes the rest of the buffer while reporting the requested position). Must work before and
   after `rollover()`.
5. Seeking to a negative absolute position (`seek(-1, SEEK_SET)`, or `seek(-3, SEEK_CUR)` from position 2) must
   raise `ValueError` without moving: `tell()` stays `2` and `read()` returns `"bc"`.''')

G("fresh_boltons_443",
  "Six independent bugs across five modules (tableutils, timeutils isoparse + daterange, strutils, jsonutils, "
  "iterutils): two hangs, three crashes and one silent data corruption, each with its own pinned semantics.",
  r'''boltons edge cases: hangs, crashes and a silent corruption across several utilities

A review of boltons' edge cases found these bugs; please fix all of them:

1. `tableutils.Table.to_text()` crashes on degenerate input. Expected: `Table([[]]).to_text() == ""`;
   `Table([[None, "b"], [1, 2]]).to_text()` renders a `None` header cell as `None` (same as a `None` data cell);
   with empty headers there is no header row and no separator rule (`Table([[], [1]])` has `headers == []` and
   `to_text() == "1"`); headers shorter than the table width are padded with `None` cells (headers `["a"]` on a
   2-column table -> header cells `["a", "None"]`). This mirrors what `to_html` already does.
2. `timeutils.isoparse` parses fractional seconds as a literal integer of microseconds (`.851` -> 851 µs). It must
   round-trip `datetime.isoformat()` at every timespec: `isoparse(dt.isoformat(timespec="milliseconds")) == dt` for
   `dt = datetime(2020, 1, 2, 3, 4, 5, 851000)`; `.051` -> `microsecond == 51000`, `.000001` -> `1`; more than six
   digits truncate: `.123456789` -> `123456`.
3. `timeutils.daterange` loops forever when the step cannot make progress. Expected: a stationary step raises
   `ValueError` (`step=0`, `step=(0, 0, 0)`, `step=(0, 1, -31)` from Jan 1, also with `stop=None`); a step pointing
   away from `stop` yields nothing, like `range(1, 5, -1)` (`daterange(date(2020,1,1), date(2020,1,5), step=-1)` ->
   `[]`, also `step=(0, -1, 0)`); datetimes with sub-day `timedelta` steps work
   (`daterange(datetime(2020,1,1,0), datetime(2020,1,1,12), step=timedelta(hours=3))` -> 0h, 3h, 6h, 9h).
4. `strutils.MultiReplace({})` (and `MultiReplace([])`, `multi_replace(s, {})`) raises `KeyError: None`; an
   empty mapping must be a no-op returning the input.
5. `jsonutils.JSONLIterator` loops forever when a `rel_seek` lands in the middle of the final line of a file
   without a trailing newline (`'{"1": 1}\n{"2": 2}'` with `rel_seek=0.9` must yield `[]`), and a negative
   `rel_seek` is normalised past EOF: `rel_seek=-0.5` must behave exactly like `rel_seek=0.5` (a non-empty tail of
   the file's records).
6. `iterutils.xfrange` yields nothing for descending ranges and disagrees with `frange` on counts at inexact float
   boundaries. Expected: `list(xfrange(5, 0, step=-1.25)) == [5.0, 3.75, 2.5, 1.25]`; wrong-direction steps yield
   `[]` (`xfrange(1, 5, step=-1)`, `xfrange(5, 0)`); `len(frange(1.0, step=0.1)) == 10`,
   `len(frange(10.0, step=0.1)) == 100`; `list(xfrange(...)) == frange(...)` for the same arguments; `step=0` raises
   `ValueError` for both. `frange`'s existing output must not change.''')

G("fresh_click_3818",
  "Restructuring the exception cascade of `Command.main()` so a second Ctrl-C at any point while reporting "
  "an abort / error / success cannot escape or change the exit code, while keeping two settled contracts "
  "(non-standalone callers get `Abort` from the first interrupt; the `Aborted!` message).",
  r'''A second Ctrl-C while Click reports an abort can escape as an unhandled traceback

When a user presses Ctrl-C during `click.prompt()`, Click occasionally crashes with an unhandled exception
instead of printing `Aborted!` and exiting 1 (seen on a slow CI runner; it is a race):

```
  File "click/core.py", line 1532, in main
  File "click/utils.py", line 337, in echo
  File "click/_compat.py", line 509, in should_strip_ansi
  File "click/_compat.py", line 578, in isatty
KeyboardInterrupt
```

A late `KeyboardInterrupt` that arrives while `Command.main()` is already reporting an outcome (writing
`Aborted!`, showing an error, or exiting) escapes. Expected policy: a late interrupt must not replace the intended
outcome. In standalone mode the collected exit code always wins (aborts, errors and success alike); the message is
best effort. Without standalone mode the interrupt propagates to the caller.

Pinned behaviour (standalone mode, `cli.main([], "cli")`):
- Interrupt at a prompt (Abort raised by the prompt), with or without a second interrupt raised by the first
  `sys.stderr.isatty()` call while echoing the message: exit code 1, never a `KeyboardInterrupt` out of `main()`.
  Without a second interrupt, stderr contains `Aborted!`.
- `KeyboardInterrupt` raised in the command body: exit code 1 even if a second interrupt comes from the first or
  second `isatty()` call (Click writes a blank line first, then the message).
- A `ClickException` subclass with `exit_code = 3` whose `show()` raises `KeyboardInterrupt` -> exit code 3.
- After a successful run, if the first `sys.exit(0)` call itself raises `KeyboardInterrupt`, Click calls
  `sys.exit(0)` again: exit code 0 and `sys.exit` was called with `[0, 0]`.
- With `standalone_mode=False`, the first interrupt still reaches the caller as `click.Abort` (with the interrupt
  as `__cause__`), but if constructing/raising that `Abort` is itself interrupted, the `KeyboardInterrupt`
  propagates to the caller (not exit 1).
- The `Aborted!` text is unchanged.''')

G("fresh_click_3777",
  "Pager internals: replace substring matching with real option parsing over `$LESS` (shlex with a "
  "whitespace fallback) and the command's own arguments, stem-based `less` detection with OS-specific "
  "case rules, and forwarding of PAGER parameters in the temp-file backend; a dozen pinned edge cases.",
  r'''Pager: raw-mode (`-R`) detection matches any "r", and PAGER parameters are dropped

Click's pager decides whether `less` will show colours by looking for raw-mode flags, but the check is a substring
test over `$LESS` concatenated with the command arguments, so any `r`/`R` enables colour: `less README.md`,
`LESS=--prompt=resume`, `less requirements.txt` all count as raw mode. In addition, parameters a user puts in `PAGER`
are not forwarded by the temp-file backend, and `less.exe` is not recognised as `less`.

Expected behaviour of `click._termui_impl._pipepager(cmd_path, cmd_params)` (a context manager yielding
`(stream, color)`; `cmd_path` is the resolved pager path, `cmd_params` its extra arguments), with `subprocess.Popen`
receiving `env` - the table gives `cmd_params`, `$LESS`, the yielded `color`, and the `LESS` value in the child env
(`-` = not set):

| cmd_params | $LESS | color | child LESS |
|---|---|---|---|
| [] | unset | True | `-R` (injected) |
| ["-R"] | unset | True | - |
| ["-rX"] (bundled short flags) | unset | True | - |
| [] | `-R` | True | `-R` |
| ["--RAW-CONTROL-CHARS"] | unset | True | - |
| ["-X"] | unset | None | - |
| [] | `--prompt=resume` | None | `--prompt=resume` |
| ["README.md"] | unset | None | - |
| ["requirements.txt"] | unset | None | - |
| ["--", "README.md"] (option terminator) | unset | None | - |
| ["--raw-control-chars=x"] (value, not the bare option) | unset | None | - |
| [] | `-R "` (unbalanced quote: fall back to whitespace splitting) | True | `-R "` |

`None` means "no opinion", which `get_pager_file()` settles to off. Recognised raw options: `-r`, `-R` (also
bundled in short-flag groups) and the long forms `--raw-control-chars` / `--RAW-CONTROL-CHARS`.

`less` detection (which triggers the `LESS=-R` auto-enable) matches the command's stem: with `PAGER` pointing to
an executable named `less` or `less.exe`, `click.get_pager_file()` yields `pager.color is True`; `LESS.EXE` stays
unmatched on POSIX (`False`), and `cat` -> `False`. On Windows (`_termui_impl.WIN` True) the match is
case-insensitive, so `_pipepager(Path(".../LESS.EXE"), [])` yields `color is True`.

The temp-file pager backend must forward `PAGER` parameters: with `PAGER="head -n 1"`,
`click.echo_via_pager("line one\nline two\n")` prints just `line one\n`.''')


# per-repo goal modules register themselves via G (imported last: G must exist first)
import goals_b  # noqa: E402,F401
import goals_c  # noqa: E402,F401
import goals_d  # noqa: E402,F401
import goals_sqlglot  # noqa: E402,F401
