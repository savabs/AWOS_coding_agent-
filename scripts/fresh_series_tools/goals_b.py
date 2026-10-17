"""Goals: more-itertools, pyparsing, Python-Markdown, pathspec (registered into goals.GOALS)."""
from goals import G

G("fresh_more-itertools_1248",
  "Float-exactness bug spanning four methods of `numeric_range` (`_len`, `index`, `__contains__`, `count`) "
  "that must be made consistent with how items are produced (start + n*step) in both the over- and "
  "under-count directions, without slowing exact types.",
  r'''`numeric_range`: `len()`, `in` and `index()` disagree with the items it produces for float steps

```pycon
>>> from more_itertools import numeric_range
>>> r = numeric_range(-8.732, -13.532, -2.4)
>>> list(r)
[-8.732, -11.132]
>>> len(r)
3
>>> r[2]                      # counted, but it is the excluded stop value
-13.532

>>> r = numeric_range(-69.37276490678778, -29.8339073344664, 4.942357196540172)
>>> len(list(r)), len(r)
(9, 8)                        # r[8] -> IndexError, although iteration yields it

>>> r = numeric_range(0.0, 1.0, 0.1)
>>> r[3]
0.30000000000000004
>>> r[3] in r
False
>>> r.index(r[3])
ValueError: 0.30000000000000004 is not in numeric range
```

`numeric_range` is a `Sequence`, so it must agree with itself. Expected, for every item `v` at position `i` of
ranges such as `(0.0, 1.0, 0.1)`, `(0.0, 3.0, 0.7)`, `(1.0, 0.0, -0.1)`, `(2.1, 5.1, 0.5)`, `(-1.0, 1.0, 0.25)`:
`v in r`, `r.index(v) == i`, `r[i] == v`, `r.count(v) == 1`. Values the range does not produce stay out
(`i / 1000` for i in range(1000) that is not an item of `numeric_range(0.0, 1.0, 0.1)`: `not in r`, and
`r.index(v)` raises `ValueError`). `len(r) == len(list(r))` and `r[len(r) - 1]` is the last item for e.g.
`(-8.732, -13.532, -2.4)`, `(4.51, 10.375, 0.255)`, `(-69.37276490678778, -29.8339073344664, 4.942357196540172)`,
`(32.50227965239458, 9.003418351482358, -1.9582384417426848)`, `(0.0, 1.0, 0.1)`, `(0.0, 3.0, 0.7)`,
`(10.0, -10.0, -0.7)`, `(2.1, 5.1, 0.5)`.

Exact types (`int`, `Decimal`, `Fraction`, `datetime`/`timedelta`) must behave exactly as before and stay as fast.''')

G("fresh_more-itertools_1261",
  "Algorithmic rewrite of `combination_with_replacement_index`: the existing scan uses `None` as an exhaustion "
  "sentinel, so the fix needs a different matching strategy that handles `None` data, duplicates (first index) "
  "and invalid combinations.",
  r'''`combination_with_replacement_index` mistakes `None` values for exhaustion

`more_itertools.combination_with_replacement_index(element, iterable)` returns the wrong index when the element
contains `None`, and accepts some combinations containing `None` that are not combinations of the iterable:

```python
from itertools import combinations_with_replacement
from more_itertools import combination_with_replacement_index

pool = [1, None, 2]
list(combinations_with_replacement(pool, 1)).index((None,))   # 1
combination_with_replacement_index((None,), pool)            # 2   (wrong)
combination_with_replacement_index((None,), [1, 2])          # 1   (should raise ValueError)
```

Expected:
- For iterables like `[None, 1, 2]`, `[1, None, 2]`, `[1, 2, None]`, `[None, 1, None]` and every `r` in 0..3, for
  each element of `combinations_with_replacement(iterable, r)` the function returns the index of the *first*
  occurrence of that element in that sequence. It must work when both `element` and `iterable` are one-shot
  iterators.
- Invalid combinations raise `ValueError`: `((None,), [1, 2])`, `((1, None), [1, 2])`, `((1, None), [None, 1])`,
  `((None, 2), [1, None])`.
- Existing behaviour for ordinary inputs is unchanged.''')

G("fresh_pyparsing_653",
  "Redesign of a stateful helper: `match_previous_expr` must keep a stack of pending matches registered once "
  "(instead of accumulating parse-action guards), pair inner with nearest outer matches, survive backtracking "
  "and memoization, and keep the exact exception message.",
  r'''`match_previous_expr` fails for nested and repeated matches

`pyparsing.match_previous_expr(expr)` should be usable to enforce matching open/close tags, but it fails as soon
as there is more than one match of the leading expression:

| input | expected | today |
|---|---|---|
| `{a}xx{/a}` | match | match |
| `{a}{b}xx{/b}{/a}` | match | fails |
| two sequential pairs, `a:a b:b` under `OneOrMore` | match | fails |
| re-parsing valid input after one failed parse | match | fails |

Each successful match also leaves state behind on the repeated expression, which grows without bound.

Expected (without packrat / left recursion enabled):

```python
import pyparsing as pp
LBRACE, RBRACE, SLASH = map(pp.Suppress, "{}/")
tag_name = pp.Word(pp.alphanums + "_")
open_tag = LBRACE + tag_name + RBRACE
close_tag = LBRACE + SLASH + pp.match_previous_expr(tag_name) + RBRACE
content = pp.Forward()
content <<= (open_tag + pp.Group(content) + close_tag) | pp.Word(pp.alphas)
```
`content.parse_string(s, parse_all=True)` succeeds for `{a}xx{/a}`, `{a}{b}xx{/b}{/a}`, `{a}{b}{c}xx{/c}{/b}{/a}`
and raises `ParseException` for `{a}{b}xx{/a}{/b}`, `{a}{b}xx{/b}{/c}`, `{a}xx{/b}` (an inner close pairs with the
nearest enclosing open).

With `first = pp.Word(pp.alphas); expr = first + pp.Suppress(":") + pp.match_previous_expr(first)`:
`pp.OneOrMore(expr).parse_string("a:a b:b c:c")` -> `["a", "a", "b", "b", "c", "c"]`; then `d:d` parses to
`["d", "d"]`, `e:f` raises `ParseException`, and `g:g` still parses afterwards. Matches left behind by a branch
that was backtracked out of must never be paired, state must not grow across parses, `enable_packrat()` /
`enable_left_recursion()` must not regress, and the `ParseException` message stays byte-for-byte the same.''')

G("fresh_pyparsing_652",
  "Core `ParserElement.__mul__` change: build the optional tail of `expr[..., n]` / `expr * (0, n)` without "
  "n-deep recursion at construction or parse time, while still stopping at the first failed slot "
  "(a naive flat list of Optionals is rejected by a test) and enforcing the upper bound.",
  r'''`expr[..., upper_bound]` raises RecursionError for a large upper bound

`pp.Literal("A")[..., 1000]` (and `pp.Word(pp.nums) * (0, 1000)`) raises `RecursionError`, because the bounded
repetition builds a nested expression `upper_bound` levels deep.

Expected:
- With `sys.setrecursionlimit(300)`, both `pp.Literal("A")[..., 1000]` and `pp.Word(pp.nums) * (0, 1000)` can be
  constructed, and `pp.Literal("A")[..., 1000]` parses `"A A A A A"` to `["A", "A", "A", "A", "A"]` (normal limit).
- The upper bound is still enforced: `(pp.Word(pp.alphas)[..., 3] + pp.Word(pp.nums)).parse_string("a b c d 1")`
  raises `ParseException`.
- Parsing must still stop at the first slot that fails to match, like the nested form did: with
  `A_expr = pp.Literal("A").set_fail_action(count_fails)` and `expr = A_expr[..., 1000]`, `expr.parse_string("A")`
  calls the fail action exactly once (not once per remaining slot).
- Results collection (`list_all_matches`) and all existing repetition / multiplication behaviour stay unchanged.

As part of this, `OneOrMore` and `ZeroOrMore` get an optional `max` keyword (a bounded loop is a natural building
block for the optional tail):
- `pp.OneOrMore(pp.Word(pp.nums), max=3)` parses `"1"`, `"1 2"`, `"1 2 3"` to 1/2/3 tokens and parses only
  `["1", "2", "3"]` out of `"1 2 3 4 5"` (the rest is left unparsed); with `max=1`, `"1 2 3"` gives one token.
- `pp.ZeroOrMore(pp.Word(pp.nums), max=3)` additionally parses `""` to zero tokens; same capping.
- `max=0` or a negative `max` raises `ValueError` for both classes; `max=None` (default) means unbounded.''')

G("fresh_markdown_1648",
  "HTML extractor state machine change in two parsers (core `htmlparser` and the `md_in_html` extension): track "
  "raw inline tags open in the current paragraph so a comment is kept inline only when the tag closes later in "
  "the same paragraph - nine pinned cases, most of which must keep today's output.",
  r'''An HTML comment inside inline raw HTML splits the paragraph

A raw HTML comment on its own line inside an inline element is always treated as a block, which breaks the inline
element across paragraphs:

```markdown
<span>
<!-- comment -->
</span>
```
renders as `<p><span></p>\n<!-- comment -->\n<p></span></p>`. It should render as
`<p><span>\n<!-- comment -->\n</span></p>` (what markdown.pl does). Real-world case: a comment inside `<picture>`.

Expected (input -> output), both with plain Markdown and with the `md_in_html` extension:
- `<span>\n<!-- comment -->\n</span>` -> `<p><span>\n<!-- comment -->\n</span></p>`
- `<span><!-- comment -->\n</span>` -> `<p><span><!-- comment -->\n</span></p>`
- `<span>\n<!-- comment --></span>` -> `<p><span>\n<!-- comment --></span></p>`
Unchanged (must keep today's output):
- blank lines around the comment still split: `<span>\n\n<!-- comment -->\n\n</span>` -> `<p><span></p>\n<!-- comment -->\n\n<p></span></p>`
- a comment containing a blank line still splits: `<span>\n<!--\nmulti\n\nline\n-->\n</span>` -> `<p><span></p>\n<!--\nmulti\n\nline\n-->\n<p></span></p>`
- after an already closed tag: `<span>foo</span>\n<!-- comment -->` -> `<p><span>foo</span></p>\n<!-- comment -->`
- after a tag in a code span: ``Use `<b>` for bold.\n<!-- comment -->`` -> `<p>Use <code>&lt;b&gt;</code> for bold.</p>\n<!-- comment -->`
- after a tag that is never closed: `<span>foo\n<!-- comment -->` -> `<p><span>foo</p>\n<!-- comment -->`
- after a void tag: `<img src="a.png">\n<!-- comment -->` -> `<p><img src="a.png"></p>\n<!-- comment -->`

So: treat the comment as inline only when an inline raw tag open in the current paragraph is closed later in the
same paragraph and the comment has no blank line in it.''')

G("fresh_pathspec_157",
  "Regex-translation change in the shared gitignore pattern translator: escape-aware scanning for the closing "
  "bracket plus a single-pass tokenizer for bracket bodies that interleaves escapes with POSIX classes; "
  "eleven git-verified pattern cases.",
  r'''gitignore patterns: a backslash inside a bracket expression is not treated as an escape

In git, a backslash inside a bracket expression escapes the next character, just as outside one. pathspec treats
it as a literal backslash, so valid patterns match the wrong files:

| pattern | git matches | pathspec matches |
|---|---|---|
| `[a\-c]` | `a`, `-`, `c` | `a`, `b`, `c`, `\`, `]`, `^` (read as the range `\-c`) |
| `[\]]` | `]` | `\]` |
| `[\]a]` | `]`, `a` | nothing |
| `[!\]]` | any one character except `]` | `]]` |
| `[\n]` | `n` | `n`, `\` |

Expected for both `GitIgnoreSpecPattern` and `GitIgnoreBasicPattern` (pattern: matches / does not match; Python
notation for the strings):
- `'[a\\-c]'`: `a`, `-`, `c` / `b`, `\\`
- `'[\\]]'`: `]` / `\\`, `\\]`
- `'[\\]a]'`: `]`, `a` / `\\`, `\\]a`
- `'[a\\]]'`: `a`, `]` / `\\`, `\\]`
- `'[!\\]]'`: `a`, `\\` / `]`
- `'[\\!a]'`: `!`, `a` / `\\`
- `'[\\^a]'`: `^`, `a` / `\\`, `b`
- `'[\\n]'`: `n` / `\\`, `\n`
- `'[\\\\]'`: `\\` / `a`
- `'[\\[:alpha:]]'` (escaped `[`, so no POSIX class): `[]`, `:]`, `a]` / `a`, `b`, `\\`
- `'[[:alpha:]\\]]'`: `a`, `]` / `5`, `\\`

POSIX classes such as `[[:alpha:]]` must keep working. Reversed ranges (`[z-a]`) are out of scope.''')

G("fresh_pathspec_152",
  "Seven files across the pattern classes and all three regex backends: switch the generated end anchor to a "
  "strict end-of-string anchor (`\\Z`), translate it for RE2/Hyperscan (`\\z`) without corrupting an escaped "
  "literal backslash-Z, and keep directory-marker splitting intact; exact regex text pinned.",
  r'''gitignore: a pattern `foo` also matches a file named `foo\n`

A gitignore rule such as `foo` matches a path named `foo\n` (a filename ending in a newline). Git keeps the newline
as part of the filename, so `foo` must not match it. The problem is the end anchor in the generated regular
expressions: Python's `$` also matches just before a final newline.

Expected:
- Generated regexes end with Python's strict end anchor `\Z` instead of `$`. E.g. for `GitIgnoreBasicPattern`
  the optional directory ending is `(?:/|\Z)` (it was `(?:/|$)`), and `GitIgnoreBasicPattern('!libfoo/*').regex.pattern
  == r'^libfoo/[^/]+/?\Z'`. The same applies to `GitIgnoreSpecPattern`.
- For both pattern classes, str and bytes, each of these (pattern, path) pairs matches the path but NOT
  `path + "\n"`: `('foo', 'foo')`, `('foo', 'x/foo')`, `('foo', 'x\ny/foo')`, `('/foo', 'foo')`, `('**/foo', 'x/foo')`,
  `('foo?', 'foo1')`, `('foo[0-9]', 'foo1')`, and the escaped literal `(r'foo\\Z', r'foo\Z')`.
- Specs: `PathSpec.from_lines(<pattern class>, ['foo'], backend=b)` and `GitIgnoreSpec.from_lines(['foo'], backend=b)`
  match `'foo'` but not `'foo\n'`; `GitIgnoreSpec.from_lines(['*', '!foo'])` does not match `'foo'` but matches
  `'foo\n'`; `PathSpec.from_lines(cls, [r'foo\\Z'])` matches `r'foo\Z'` and not `r'foo\z'` (with `separators=('/',)`).
- The RE2 and Hyperscan backends compile Python-style patterns: translate the strict anchor to the spelling they
  understand (`\z`) before compiling, without touching an escaped literal backslash followed by `Z`.''')

G("fresh_pathspec_144",
  "Match-resolution change in all three gitignore backends: the excluded-ancestor rule must only consider strict "
  "ancestors and only fire when the whole spec really excludes that ancestor (outermost first), even though a "
  "single pattern can match both an ancestor and the path itself.",
  r'''GitIgnoreSpec: excluded-directory rule fires on an ancestor that the spec re-includes

```python
from pathspec import GitIgnoreSpec
GitIgnoreSpec.from_lines(['.*', '!**/node_modules/**']).match_file('vendor/deps/npm/node_modules/.bin/x.txt')
# True, but git stages this file (it is not ignored)
```

pathspec applies git's rule "a file cannot be re-included if a parent directory is excluded", but it decides that
an ancestor (`.bin/`) is excluded from the one pattern that produced a directory match (`.*`), without checking
whether the spec as a whole re-includes that ancestor (`!**/node_modules/**` does).

Expected, for the spec `['.*', '!**/node_modules/**']` with every backend (verified with git 2.55 via
`check-ignore -v` and `git add -A`): of the files
`.hidden`, `vendor/keep.txt`, `vendor/.cache/y.txt`, `vendor/deps/npm/node_modules/keep.txt`,
`vendor/deps/npm/node_modules/.bin/x.txt`, `vendor/deps/npm/node_modules/.bin/.hide.txt`, `node_modules/.bin/z.txt`,
exactly `.hidden` and `vendor/.cache/y.txt` are ignored (`spec.check_files(files)` reports include=True only for those).

The excluded-ancestor rule should only consider strict ancestors of the path, and only fire when that ancestor is
excluded by the whole spec (checked outermost first, the order in which git stops descending). Existing idioms must
keep working, e.g. `!*/` ("scan all directories"), `*` + `!libfoo` + `!libfoo/**`, and `build/*` + `!build/keep.log`.''')

G("fresh_pathspec_132",
  "Core gitignore match resolution in all three backends: replace flat last-match with git's hierarchical rule "
  "(a file under an excluded directory cannot be re-included), resolving each ancestor as a directory so "
  "directory-level re-inclusions and existing whitelist idioms keep working.",
  r'''GitIgnoreSpec re-includes files under an excluded directory (violates git's parent-exclusion rule)

`GitIgnoreSpec` re-includes a file through a negation pattern even when a parent directory is excluded, which git
forbids ("It is not possible to re-include a file if a parent directory of that file is excluded"):

```python
from pathspec import GitIgnoreSpec
spec = GitIgnoreSpec.from_lines(["build", "!keep.log"])
spec.match_file("build/keep.log")   # -> False, but git treats build/keep.log as ignored
```

Excluding a directory is different from excluding a directory's contents:

| `.gitignore` | path | git | pathspec today |
|---|---|---|---|
| `build` + `!keep.log` | `build/keep.log` | ignored | not ignored |
| `a` + `!keep.log` | `a/keep.log` | ignored | not ignored |
| `build/*` + `!build/keep.log` | `build/keep.log` | re-included | re-included |

Expected (all backends; `spec.check_files(files)` results, verified with git 2.54):
- `["build", "!keep.log"]`: `build/keep.log` ignored, `keep.log` not ignored.
- `["build", "!build/keep.log", "a", "!a/b/keep.log"]`: both `build/keep.log` and `a/b/keep.log` ignored.
- `["build/*", "!build/keep.log", "log", "!log/", "!log/keep.log"]`: only `build/drop.log` ignored; `build/keep.log`
  and `log/keep.log` are re-included (the directory itself is not excluded, or its exclusion is undone first).
- Existing idioms keep working: `*` + `!libfoo` + `!libfoo/**`, `!*/`, `!*.yaml/`.''')
