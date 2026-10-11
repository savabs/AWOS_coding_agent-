"""Goals: astroid (registered into goals.GOALS)."""
from goals import G

G("fresh_astroid_3348",
  "Cycle detection in astroid's live-object conversion (`const_factory` and its dict/sequence helpers): track the "
  "containers currently being converted, turn back-references into `EmptyNode`s that still infer to the container "
  "class, and keep shared (non-cyclic) containers converting normally.",
  r'''RecursionError when astroid builds a module from a live object containing self-referencing containers

When astroid builds a module from a live object (C extensions, or `--extension-pkg-allow-list` in pylint),
`nodes.const_factory` converts the builtin containers it finds element by element. A container that holds itself,
directly or through other containers, makes it recurse until `RecursionError`. Since scipy 1.16,
`scipy.special._orthogonal` keeps `_modattrs = globals()`, so `pylint -E --extension-pkg-allow-list=scipy` on a
file with `from scipy.special import erf` crashes (the traceback alternates `const_factory` and
`_create_dict_items`).

Expected:
- A reference back to a container that is currently being converted becomes an `nodes.EmptyNode` whose `object`
  is that container (as objects of other types already are), and it infers to an instance of the container's class.
- Example: `loop = [1]; loop.append(loop); mapping = {"loop": loop}; mapping["mapping"] = (mapping,)`;
  `node = nodes.const_factory(mapping)` is a `nodes.Dict`; its first value is a `nodes.List` whose elements are
  `Const(1)` and an `EmptyNode` with `.object is loop`; its second value is a `nodes.Tuple` whose element is an
  `EmptyNode` with `.object is mapping`.
- A container referenced twice without a cycle is converted normally both times:
  `nodes.const_factory([shared, shared])` with `shared = [1]` gives a `List` of two `List`s with value `1`.
- Building a module object: for a `types.ModuleType` whose namespace contains itself under `"namespace"` and a
  self-containing list under `"loop"`, `AstroidBuilder(AstroidManager()).inspect_build(module, "test")` works;
  `node.locals["namespace"][0]` is a `Dict` whose `"namespace"` value is an `EmptyNode` (`.object is namespace`)
  and whose `"loop"` value is a `List`; `node.locals["loop"][0]` is a `List` with one `EmptyNode` element
  (`.object is loop`) that infers to exactly one value with `pytype() == "builtins.list"`.
- Data without cycles converts exactly as before.''')

G("fresh_astroid_3286",
  "Raw-building fix in astroid's live-object introspection: re-parenting of already-built class nodes must never "
  "close a parent cycle (a class exposing itself or a sibling as an attribute), which otherwise makes "
  "`NodeNG.root()` loop forever - the name must still be registered.",
  r'''Linting a module with pyo3 complex enums hangs (raw building creates a parent cycle)

pylint hangs when linting code that imports a compiled module with a pyo3 *complex enum*: every variant is attached
to the enum class, so each variant also sees itself and its siblings through inheritance. The hang is in the
`while parent.parent` walk of `NodeNG.root()`. Pure-Python reproduction:

```python
import sys, types, astroid
fruit_basket = types.ModuleType("fruit_basket")
class Fruit: ...
class Apple(Fruit): ...
class Banana(Fruit): ...
Fruit.Apple = Apple
Fruit.Banana = Banana
for klass in (Fruit, Apple, Banana):
    klass.__module__ = "fruit_basket"
    setattr(fruit_basket, klass.__name__, klass)
sys.modules["fruit_basket"] = fruit_basket
astroid.MANAGER.ast_from_module(fruit_basket, "fruit_basket")   # hangs / never terminates
```

Expected, for a module built like the above (`ComplexEnum` with variants `FirstVariant`, `SecondVariant` attached
as class attributes) via `AstroidBuilder(AstroidManager()).inspect_build(module, modname=...)`:
- building terminates;
- `built["FirstVariant"]` is a `nodes.ClassDef`, `variant["FirstVariant"] is variant` (the name is still registered
  in its locals), `variant.parent is not variant`, and `variant.root() is built`;
- walking `.parent` from `ComplexEnum`, `FirstVariant` or `SecondVariant` never revisits a node (no parent cycle).''')

G("fresh_astroid_3235",
  "`as_string()` for f-strings must choose a delimiter (or escape the literal parts) so the output re-parses to "
  "the same JoinedStr even when merged adjacent f-strings contain every quote style, including inside expression parts.",
  # TSQ / TDQ stand for three single / double quotes (they cannot appear literally in this source string)
  r"""`JoinedStr.as_string()` does not round-trip when the f-string contains all quote styles

`AsStringVisitor` renders an f-string by joining its literal and `{expression}` parts and then picking a surrounding
quote from `'`, `"`, `TSQ`, `TDQ` - the first one not present in the rendered body. When adjacent f-strings are
merged into one `JoinedStr` whose parts together contain every candidate quote style, the chosen delimiter reappears
in the body: the f-string terminates early and the tail re-parses as a different expression (a body like
`ATSQ+BAD+TSQB` can even inject an operator).

Expected: `as_string()` of an f-string always re-parses to a `nodes.JoinedStr`, and re-rendering it gives the same
string. Cases (source given to `astroid.extract_node`):
- `f'TDQ{v}' f"TSQ"` -> `reparsed = astroid.extract_node(node.as_string())` is a `JoinedStr` and
  `reparsed.as_string() == node.as_string()`.
- `f"ATSQ+BAD+TSQB{v}" f'TDQ'` -> `astroid.extract_node(node.as_string())` is a `JoinedStr`.
- `f"TSQ" f'TDQ' fTSQ{d["it's"]}TSQ` (both single-character quotes occur inside the expression part, so a
  triple-quoted delimiter is needed) -> re-parses to a `JoinedStr` with identical `as_string()`.
- Existing outputs stay, e.g. `f'3={3!r}'` debug-mode rendering.""".replace("TSQ", "'" * 3).replace("TDQ", '"' * 3))

G("fresh_astroid_3130",
  "Inference safety: compute the size of old-style `%` formatting output from the conversion specs (width, precision, "
  "`*` arguments, mapping keys with nested parens, length modifiers, bytes, augmented assignment) before evaluating, "
  "in two code paths (the dedicated handler and the binary-op protocol).",
  r'''Inferring old-style `%` formatting can materialise gigantic strings

astroid evaluates old-style `%` string formatting eagerly during inference (`template % values`) without bounding
the output. Width and precision come straight from the conversion specs, so a 17-character literal like
`"%1000000000d" % 1` builds a 1 GB `Const` while inferring (pylint then hangs / runs out of memory).

Expected: when the formatted result would be oversized, inference yields `util.Uninferable` instead of computing
it. All of these infer to `Uninferable`:
`"%1000000000d" % 1`, `"%-1000000000s" % "x"`, `"%.1000000000f" % 1.0`, `"%*s" % (1000000000, "x")`,
`"%*s" % (-1000000000, "x")`, `"%.*f" % (1000000000, 1.0)`, `"%(x)1000000000s" % {"x": "y"}`,
`"%((x))1000000000s" % {"(x)": "y"}`, `b"%1000000000d" % 1`, and `x = "%1000000000d"; x %= 1; x`.

Normal formatting still infers:
- `"%ld" % 5` -> `Const("5")` (h/l/L length modifiers are skipped and do not affect the cap);
- `"%*d %d" % (5, 7, 200000001)` -> `Const("    7 200000001")` (only arguments consumed by a `*` width/precision
  count toward the cap, not ordinary values).

The same guard must also apply on the generic binary-operation path: calling
`parsed.left.infer_binary_op(parsed, "%", parsed.right, InferenceContext(), None)` for `"%1000000000d" % 1`
yields `[Uninferable]`. (The existing `([1] * 50000001) + ([1] * 50000001)` guard is the model to follow.)''')

G("fresh_astroid_3193",
  "Security-relevant fix spanning `manager` and `modutils`: pass the resolved file location into the extension-loading "
  "decision and only trust a stdlib *name* when the file really lives in a stdlib directory (excluding site-packages "
  "below it), with a new public helper `modutils.is_stdlib_path`.",
  r'''astroid imports (executes) a C extension that merely shares a stdlib module's name

When astroid resolves a module, it decides whether it may import a C extension by name only: any extension whose
name matches a stdlib module is imported no matter where it was found. Because the analysed project's directory
comes first on `sys.path` and extension suffixes are tried before `.py`, a `colorsys.cpython-313-x86_64-linux-gnu.so`
committed to a repository resolves ahead of the real module and gets imported - running its init code - even with
`always_load_extensions` off and an empty extension allow-list. Linting a checkout can execute code from it.

Expected:
- An extension named like a stdlib module but located outside the standard library is stubbed, not imported: with an
  empty file `colorsys<EXTENSION_SUFFIXES[0]>` in a temp dir inserted at `sys.path[0]`,
  `manager.ast_from_module_name("colorsys")` returns a module named `colorsys` with `ast.items() == []`.
- `AstroidManager._can_load_extension(modname, location)` takes the resolved location as a second argument; a stdlib
  name with no resolved location is not trusted: `manager._can_load_extension("colorsys", None)` is `False`.
- New helper `astroid.modutils.is_stdlib_path(path) -> bool`: true for files inside the standard library
  directories (`modutils.is_stdlib_path(os.__file__)` is True), false for anything under `modutils.EXT_LIB_DIRS`
  (site-packages can sit below a stdlib directory in a virtualenv), e.g. `os.path.join(ext_lib_dir, "colorsys" + suffix)`.
- Extensions shipped with the interpreter (lib-dynload) still load, and an explicit allow-list entry still wins.''')

G("fresh_astroid_2977",
  "New feature in astroid's constraint-based inference: compound `and`/`or` constraints that recursively combine "
  "child constraints (nested and n-ary BoolOps, precedence), stay conservative when a child is unknown, and are "
  "ignored when an operand does not constrain the target name.",
  r'''Inference should narrow values through `and` / `or` conditions

astroid's constraint-based inference narrows a name inside `if` branches for single conditions (`x is None`,
`x == 3`, `isinstance(x, int)`, `not x`, ...), but not for compound conditions joined with `and` / `or`:

```python
def foo(x=3):
    if isinstance(x, int) and x != 3:
        x  # value 3 should be filtered out
def bar(x=3):
    if isinstance(x, str) or x != 3:
        x  # value 3 should be filtered out
```

Expected (same semantics single constraints already have: the default value is kept or filtered, and an
`Uninferable` is always added for a parameter): for `def f(x=<v>): if <cond>: x  else: x`, the value is filtered
from a branch when the compound condition is known to be false (resp. true) for it, and kept otherwise. Pinned
conditions, written as (condition, a value satisfying it, a value failing it):
- `isinstance(x, int) and x == 3` (3, 5); `isinstance(x, str) or x == 3` (3, None)
- `x is not None and (isinstance(x, int) and x == 3)` (3, 5)
- `x is not None and x and isinstance(x, int) and x == 3` (3, 0)
- `x is None or (isinstance(x, str) or x == 3)` (3, 5)
- `x is None or not x or isinstance(x, str) or x == 3` (0, 5)
- `x is not None and (isinstance(x, bool) or x == 3)` (True, 5)
- `x is None or (isinstance(x, int) and x == 3)` (3, 5)
- `x == 3 or isinstance(x, int) and x == 5` (3, None) - `and` binds tighter than `or`
For the failing default the if-body infers to just `[Uninferable]` and the else-branch to `[Const(fail), Uninferable]`;
for the satisfying default the if-body infers to `[Const(satisfy), Uninferable]` and the else-branch to `[Uninferable]`.

Conservative cases:
- If a child constraint cannot be determined (`isinstance(x, classinfo) and x == 3`, `isinstance(x, classinfo) or x == 5`
  with an un-inferable `classinfo` parameter), nothing is narrowed: both branches infer `[Const(3), Uninferable]`.
- If an operand does not constrain the target name, the whole expression is ignored: with `x, y = <value>, None`,
  `if not x and y: x` infers `[Const(value)]` (also for `x is not None and (not x and y)` with value 3 or None,
  `not x or y`, and `x is None or (not x or y)`).''')
