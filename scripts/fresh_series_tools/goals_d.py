"""Goals: networkx, pydash, tinydb, markdown-it-py, pyflakes, typeguard, tomlkit (into goals.GOALS)."""
from goals import G

G("fresh_networkx_8892",
  "Memory-model change in networkx's core classes (`graph.py`, `reportviews.py`, ~110 lines): cached edge/degree views "
  "must hold the graph weakly yet stay usable when the graph was a temporary, after pickling, and after deepcopy "
  "(rebinding to the copy), across Graph/DiGraph/MultiGraph/MultiDiGraph.",
  r'''Reading a graph's cached views creates a reference cycle (dead graphs pile up until a full GC)

`G.edges`, `G.degree` and friends are cached views that hold a strong reference to the graph, while the graph holds
the cached view: reading any of them (even indirectly, e.g. `G.number_of_edges()` reads `G.degree`, `str(G)` calls
`number_of_edges()`) puts `G` in a reference cycle. Such graphs are only reclaimed by the cyclic garbage collector,
which in long-running services with large payloads in the graph lets hundreds of MB of dead graphs accumulate.

```python
>>> import gc, weakref, networkx as nx
>>> gc.disable()
>>> G = nx.DiGraph([(0, 1)])
>>> r = weakref.ref(G)
>>> G.number_of_edges()
>>> del G
>>> r() is None
False          # should be True
```

Expected (for `nx.Graph`, `nx.DiGraph`, `nx.MultiGraph`, `nx.MultiDiGraph` and subclasses):
- With `gc.disable()`, after reading `nodes`, `adj`, `edges`, `in_edges`, `out_edges`, `degree`, `in_degree`,
  `out_degree` (where present), `G.number_of_edges()` and `str(G)`, `del G` frees the graph (`weakref.ref(G)()` is None).
- Views keep working when their graph was only a temporary: `dict(nx.Graph(edges).degree(weight="w"))`,
  `nx.Graph(edges).degree([1])`, `nx.Graph(edges).degree(1, weight="w")`, `list(nx.Graph(edges).edges([1]))`,
  `list(nx.Graph(edges).edges(data=True, nbunch=[1]))`, `G.copy().degree(...)`, `G.subgraph([0, 1]).degree([0]) -> {0: 1}`
  all give the same answers as on a live graph.
- `copy.deepcopy(G)` after the caches were populated: `D.edges._graph is D` and `D.degree._graph is D` (not `G`);
  mutating `D` updates its own views. `pickle.loads(pickle.dumps(G, -1))`: `H.edges._graph is H`, `H.degree._graph is H`.
- An unpickled view (`pickle.loads(pickle.dumps(G.edges, -1))`, likewise `G.degree`), even of a graph that no longer
  exists, still answers `ev([1])`, `ev.data("w", nbunch=[1])`, `dv(weight="w")`, `dv(1)` like the original, and can be
  pickled again.
- Existing view caching / reset behaviour (`id(G.nodes) == id(G.nodes)`, cache reset when `G._node` is replaced) is unchanged.''')

G("fresh_networkx_8837",
  "Graph-algorithm correctness across `connectivity.py` and `cuts.py`: Esfahanian's algorithm is stated for undirected "
  "graphs only, so digraph support needs min(in, out)-degree bounds, de-duplicated neighbours, the start node tried "
  "as both source and target, and the s-t shortcut checked in one direction; also self-loops / parallel edges.",
  r'''`node_connectivity`, `minimum_node_cut` and `minimum_st_node_cut` are wrong on directed graphs

```python
>>> import networkx as nx
>>> nx.minimum_node_cut(nx.DiGraph([(0, 1), (1, 2), (2, 0)]))       # directed triangle has connectivity 1
set()
>>> nx.node_connectivity(nx.DiGraph([(0, 3), (1, 2), (2, 1), (3, 0), (3, 1), (3, 2)]))   # not strongly connected
1                                                                   # should be 0
>>> G = nx.complete_graph(5); G.add_edges_from((u, u) for u in G)
>>> nx.node_connectivity(G)                                          # K5 has connectivity 4
6
```

`minimum_node_cut` can even return a set that is not a cut. Node connectivity of a digraph is a minimum over
*ordered* pairs (the two orders need not agree) and is bounded by the smaller of a node's in-degree and out-degree;
self-loops and parallel edges never add connectivity.

Expected (for every flow function in `networkx.algorithms.flow`):
- `nx.node_connectivity(nx.DiGraph([(0, 1), (1, 2), (2, 0), (3, 0)]))` == 0 and `nx.minimum_node_cut(...)` == `set()`
  (weakly but not strongly connected).
- `nx.DiGraph([(0, 3), (1, 2), (2, 1), (3, 0), (3, 1), (3, 2)])`: `local_node_connectivity(G, 0, 1) == 1`,
  `local_node_connectivity(G, 1, 0) == 0`, and `node_connectivity(G) == 0`.
- The strongly connected digraph with edges (0,1),(0,2),(0,3),(1,0),(1,2),(2,0),(2,5),(3,4),(3,5),(4,0),(4,1),(4,2),
  (4,3),(4,5),(5,3),(5,4) has `node_connectivity == 1`, and `minimum_node_cut` returns one node whose removal leaves
  it not strongly connected.
- Directed triangle: `minimum_node_cut` has exactly `node_connectivity(G) == 1` node and removing it breaks strong
  connectivity; `minimum_st_node_cut(G, 0, 2) == {1}` (an edge from t to s is not an s-t path), while
  `minimum_st_node_cut(G, 0, 1) == set()` (direct edge s->t).
- K5 with a self-loop on every node, and K5 as a `MultiGraph` with every edge duplicated: `node_connectivity == 4`
  for the graph and for `.to_directed()`; `len(minimum_node_cut(...)) == 4` for the self-loop graph and its directed version.
- Undirected results are unchanged.''')

G("fresh_pydash_273",
  "Small but cross-module: `max_by` / `min_by` must hand the default to the builtin `max`/`min` without evaluating "
  "the iteratee on it, keep the no-default `ValueError` and dict-value iteration, and drop the now-unused helper.",
  r'''`max_by` / `min_by` apply the iteratee to the default value of an empty collection

```python
import pydash as _
_.max_by([], lambda item: item["score"], default=None)   # TypeError: 'NoneType' object is not subscriptable
```
The documented `default` (which may have a different type than the items) should be returned as-is for an empty
collection, without calling the iteratee.

Expected, for both `_.max_by` and `_.min_by`:
- For an empty list, empty dict, or empty iterator, `function(empty, iteratee, default=d) is d` (`d` = `None` or any
  object) and the iteratee is never called.
- Without a default, an empty collection raises `ValueError` (list, dict and iterator).
- Non-empty collections with `default=None` work as before, for iterators and for dicts (values are compared):
  `_.max_by(iter([{"score": 1}, {"score": 2}]), lambda v: v["score"], default=None) == {"score": 2}`, same for
  `dict(enumerate(values))`; `min_by` gives `{"score": 1}`.''')

G("fresh_tinydb_634",
  "State-machine fix in the caching middleware: track the closed state, reject both reads and writes with a "
  "clear error after `close()`, while keeping the flush-on-close durability.",
  r'''CachingMiddleware: reads and writes after `close()` still "succeed" in memory

After `TinyDB.close()` (or leaving a `with` block), a database using `CachingMiddleware(JSONStorage)` still accepts
`insert` / `all` etc. against its in-memory cache. The underlying file is already closed, so those writes are silently
lost - plain `JSONStorage` fails on post-close I/O, the middleware does not.

```python
db = TinyDB(path, storage=CachingMiddleware(JSONStorage))
db.insert({'a': 1})
db.close()
db.insert({'b': 2})   # returns 2, db.all() shows both, but the file only has {'a': 1}
```

Expected: once the middleware is closed, both reads and writes raise `ValueError` with a message containing
`closed` (`db.insert(...)` and `db.all()` after `db.close()`). Data written before closing is still flushed on close:
reopening the file with a new `TinyDB(path, storage=CachingMiddleware(JSONStorage))` returns `[{'key': 'value'}]` for a
database where only `{'key': 'value'}` was inserted before close.''')

G("fresh_markdown-it-py_433",
  "Port-fidelity bug across six modules (common utils, renderer, heading/lheading/paragraph/table rules): replace "
  "Python's `str.strip()/split()` whitespace set with CommonMark's at ten call sites, including reference-label "
  "normalisation and fence info splitting.",
  r'''markdown-it-py strips characters that CommonMark does not consider whitespace

Python's `str.strip()` / `str.split()` without arguments treat U+001C, U+001D, U+001E, U+001F and U+0085 as
whitespace. CommonMark (and JavaScript's `String.prototype.trim`, which upstream markdown-it uses) does not. As a
result markdown-it-py drops these characters from paragraphs, headings, table cells and fence info strings, and
folds distinct reference labels into one (`[a\x85b]: url` resolves a usage `[a b]` in Python but not in JavaScript).
U+FEFF must not be added to the trim set either.

Expected with `MarkdownIt("js-default")` (matches markdown-it@14.1.0), for each `char` in those five:
- `md.render(f"[a b]\n\n[a{char}b]: http://example.com") == "<p>[a b]</p>\n"` and
  `md.render(f"[a{char}b]\n\n[a b]: http://example.com") == f"<p>[a{char}b]</p>\n"` (labels are not folded);
  real whitespace still collapses: `"[a b]\n\n[a\tb]: http://example.com"` -> `<p><a href="http://example.com">a b</a></p>\n`.
- The character survives: `x{char}` -> `<p>x{char}</p>\n`, `{char}x` -> `<p>{char}x</p>\n`, `# h{char}` ->
  `<h1>h{char}</h1>\n`, `h{char}\n===` -> `<h1>h{char}</h1>\n`; in a table (`.enable("table")`) the cell keeps it.
- Real whitespace is still trimmed: `# h<c>` -> `<h1>h</h1>\n` for tab, space, U+00A0, U+2003 (em space), U+3000.
- Fence info is not split on U+0085: ` ```py\x85rest\nx\n``` ` -> `<pre><code class="language-py\x85rest">x\n</code></pre>\n`,
  while ` ```py extra ` still gives `class="language-py"`.''')

G("fresh_markdown-it-py_428",
  "Performance fix with a deterministic test: add a per-state cached `rfind` of each HTML terminator to "
  "`StateInline` and a quick-reject in the `html_inline` rule (per-construct earliest legal terminator offsets) "
  "so unterminated openers never reach the tag regex, with byte-identical output.",
  r'''Runs of unterminated inline HTML openers are quadratic

With `html=True` (the `commonmark` and `gfm-like` presets), a run of unterminated inline HTML openers is O(n²):
`"x" + "<![CDATA[" * 40000` takes ~245 s to render (`<!--` ~71 s, `<?` ~30 s, `<!a` ~3 s). Every alternative of the
inline tag regex ends in a specific terminator (`-->`, `?>`, `]]>`, `>`), and each failed attempt rescans to the end
of the input, once per opener.

Expected:
- For `src = "x" + opener * 1000` with each opener in `<![CDATA[`, `<!--`, `<?`, `<!a`, `<a`, `</a`,
  `MarkdownIt("commonmark").render(src)` never calls `markdown_it.rules_inline.html_inline.HTML_TAG_RE.match`
  (the module attribute is looked up at call time, so a test can monkeypatch it with a counting wrapper), and the
  output is `"<p>x" + opener.replace("<", "&lt;") * 1000 + "</p>\n"`.
- It must be a pure quick-reject: output never changes. The shortest legal constructs still pass through as raw HTML
  with `renderInline`: `<a>`, `<a/>`, `</a>`, `<!-->`, `<!--->`, `<!---->`, `<??>`, `<!a>`, `<![CDATA[]]>`.
  Terminated constructs still reach the regex: rendering `x <!-- c --> <?pi?> <![CDATA[d]]> <!D> <a>b</a>` calls it
  and returns `"<p>" + src + "</p>\n"`.''')

G("fresh_pyflakes_868",
  "Checker rework (~110 changed lines in pyflakes' single big module): argument handling for `cast`, `TypeVar`, "
  "`NamedTuple` and `TypedDict` must bind by keyword as well as position, follow Python-version-dependent keyword "
  "forms, avoid double-reporting, and handle the new `TypeVar(default=...)`.",
  r'''pyflakes mishandles `cast` / `TypeVar` / `NamedTuple` / `TypedDict` arguments

pyflakes treats some arguments of these typing helpers as (string) type annotations, but only by position and with a
few mistakes. Problems:
- `cast(typ='Optional[int]', val=42)` (keyword arguments) reports `Optional` incorrectly / does not treat `typ` as a
  type expression. Expected: no messages for
  `from typing import cast, Optional` + `cast(typ='Optional[int]', val=42)`.
- `from typing import cast` + `cast(undefined, 0)` must report exactly one `UndefinedName` (today the name is
  visited twice).
- `TypeVar`'s new `default=` argument must be handled like `bound=`: `T = TypeVar('T', default='Optional[int]')` with
  `Optional` imported gives no messages.
- The first argument of `TypeVar` is a plain expression: `T = TypeVar(T, int)` (T undefined) reports one `UndefinedName`.
- Keyword field forms: `NamedTuple("U", x="T")` and `TypedDict("U", x="T")` treat the string values as forward
  references only on Python versions that still support those keyword forms (NamedTuple keywords before 3.15,
  TypedDict keywords before 3.13); on newer versions the string is just a string (so `from foo import T` becomes an
  `UnusedImport`). On the Python running the tests (3.12) both use `T` (no messages).
- Existing behaviour stays: functional forms with dicts/lists (`TypedDict("x", {"x": "Y"})`,
  `NamedTuple("a", [("a", "Y")])`, `TypeVar("A", bound="C")`, `TypeVar("A", List["C"])`) report `UndefinedName` for the
  undefined forward references, quoted `cast` first arguments are checked, and renamed imports of `cast` work.''')

G("fresh_typeguard_571",
  "Type-checker fix: unwrap `ReadOnly` (PEP 705) together with `Required`/`NotRequired` in any nesting order when "
  "checking TypedDict items, for typing and typing_extensions, including string annotations, while preserving "
  "required-ness for the missing-keys check.",
  r'''typeguard never checks `ReadOnly[...]` TypedDict items

`check_typed_dict` unwraps `NotRequired`/`Required` before checking an item's value, but not `ReadOnly` (PEP 705), so
the value type of a `ReadOnly[...]` item is never checked:

```python
from typing import TypedDict
from typing_extensions import ReadOnly
from typeguard import check_type

class TD(TypedDict):
    x: ReadOnly[int]

check_type({"x": "not an int"}, TD)   # passes - should raise TypeCheckError
```

Expected (with both `typing` and `typing_extensions` as provider, skipping a provider without `ReadOnly`):
- `x: ReadOnly[int]` and `y: "ReadOnly[int]"`: `{"x": 1, "y": 2}` passes; `{"x": "foo", "y": 2}` raises `TypeCheckError`
  matching `value of key 'x' of dict is not an instance of int` (same for `y`).
- Nested qualifiers in any order are stripped down to the underlying type, preserving required-ness: in a
  `total=False` TypedDict with `a: ReadOnly[Required[int]]`, `b: Required[ReadOnly[int]]`, `c: ReadOnly[NotRequired[int]]`,
  `d: "NotRequired[ReadOnly[int]]"`: `{"a": 1, "b": 2}` and `{"a": 1, "b": 2, "c": 3, "d": 4}` pass; a `"foo"` value for
  any of a/b/c/d raises `value of key '<k>' of dict is not an instance of int`; `{"b": 2}` raises
  `is missing required key(s): "a"`.
- Import `ReadOnly` the same version-gated way as the existing qualifiers (typing on 3.13+, typing_extensions below).''')

G("fresh_tomlkit_610",
  "API change with a strict grammar: `tomlkit.integer(str)` must keep the author's text verbatim (underscores, "
  "hex/oct/bin, sign) after validating it against the TOML v1.0 integer grammar, reject everything else with "
  "ValueError, and leave `integer(int)` and the parser untouched.",
  r'''`tomlkit.integer("1_000")` loses the underscores

TOML allows underscores between digits of integers (`1_000`, `5_349_221`, `0xdead_beef`). There is no way to emit
such an integer: `tomlkit.dumps` of `tomlkit.integer("1_000")` normalises it to `1000`.

Expected:
- When `tomlkit.integer()` receives a string, the original text is kept verbatim:
  `tomlkit.integer(raw).as_string() == raw` and `int(tomlkit.integer(raw)) == int(raw, 0)` for `0`, `42`, `1_000`,
  `5_349_221`, `+1_000`, `-1_000`, `0xdead_beef`, `0xDEAD_BEEF`, `0o7_7`, `0b1_0`.
- Emission agrees with parsing: `doc["n"] = tomlkit.integer(raw)` dumps as `n = <raw>\n`, and
  `tomlkit.integer(raw).as_string() == tomlkit.parse(f"n = {raw}\n")["n"].as_string()` (e.g. `1_000`, `0xdead_beef`,
  `+1_000`, `0o7_7`).
- Strings outside the TOML v1.0.0 integer grammar raise `ValueError`: `""`, `"abc"`, `"_100"`, `"100_"`, `"1__000"`,
  `"01_2"` (leading zero), `"0x_1"`, `"0X1"` (prefix must be lowercase), `"+0x1"` and `"-0o7"` (no sign on
  hex/oct/bin), `" 1"`, `"1 "`, `"1.0"`, `"1e3"`.
- `tomlkit.integer(1000).as_string() == "1000"` (int input unchanged); the parser is unchanged.''')
