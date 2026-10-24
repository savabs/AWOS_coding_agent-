"""Goals for the remaining sqlglot fresh series (registered into goals.GOALS)."""
from goals import G

G("fresh_sqlglot_8524",
  "Three files (expression arg, Spark parser, DuckDB generator): Spark's `SLICE(arr, start, length)` must be "
  "rewritten to DuckDB's inclusive-end `ARRAY_SLICE`, with constant folding for literals, a CASE expression "
  "for negative / non-literal starts, overflow-safe BIGINT arithmetic and NULL handling - exact SQL pinned.",
  r'''Spark -> DuckDB: `slice(arr, start, length)` is transpiled as if length were an end index

```python
import sqlglot
sqlglot.parse_one("SELECT slice(split('abcdef', ''), 2, 4) AS chars", read="spark").sql("duckdb")
# SELECT ARRAY_SLICE(STR_SPLIT_REGEX('abcdef', ''), 2, 4) AS chars    -> ['b', 'c', 'd']
```

Spark's third `slice` argument is a length (1-based start, negative start counts from the end); DuckDB's
`ARRAY_SLICE` takes an inclusive end index. Spark returns `['b', 'c', 'd', 'e']`.

Expected: Spark/Spark2/Databricks `SLICE` keeps round-tripping unchanged in those dialects
(`SELECT SLICE(SPLIT('abcdef', ''), 2, 4) AS chars`), and DuckDB output is
`SELECT ARRAY_SLICE(STR_SPLIT_REGEX('abcdef', ''), 2, 5) AS chars`. The DuckDB translations below are pinned
exactly (read from spark2, spark and databricks):

- `SLICE(ARRAY(1, 2, 3), 1, 2)` -> `ARRAY_SLICE([1, 2, 3], 1, 2)`
- `SLICE(ARRAY(1, 2, 3), 2, 4)` -> `ARRAY_SLICE([1, 2, 3], 2, 5)`
- `SLICE(ARRAY(1, 2, 3), 3, 0)` -> `ARRAY_SLICE([1, 2, 3], 3, 2)` (empty slice)
- `SLICE(ARRAY(1, 2, 3), 2, 2147483647)` -> `ARRAY_SLICE([1, 2, 3], 2, 2147483648)` (no overflow)
- `SLICE(NULL, 2, 1)` -> `ARRAY_SLICE(NULL, 2, 2)`
- `SLICE(a, 2, n)` -> `ARRAY_SLICE(a, 2, (2 + CAST(n AS BIGINT)) - 1)`
- negative or non-literal starts normalise the start with a CASE, e.g. `SLICE(a, s, n)` ->
  `ARRAY_SLICE(a, CASE WHEN s < -ARRAY_LENGTH(a) THEN ARRAY_LENGTH(a) + 1 WHEN s < 0 THEN ARRAY_LENGTH(a) + s + 1 ELSE s END, (CASE WHEN s < -ARRAY_LENGTH(a) THEN ARRAY_LENGTH(a) + 1 WHEN s < 0 THEN ARRAY_LENGTH(a) + s + 1 ELSE s END + CAST(n AS BIGINT)) - 1)`;
  `SLICE(a, s, 2)` is the same with `CAST(2 AS BIGINT)`; `SLICE(ARRAY(1, 2, 3), -2, 2)` is the same shape with
  `-2` for `s`, `[1, 2, 3]` for `a` and `CAST(2 AS BIGINT)` (likewise for `-4`).

Other dialects' slicing must not change.''')

G("fresh_sqlglot_8029",
  "Four files: a shared transform that unqualifies (UN)PIVOT FOR/IN fields (refactored out of the Spark "
  "generator), wired into Oracle, plus dropping `AS` before the pivot alias - also after `qualify` rewrote the AST.",
  r'''Oracle: generated (UN)PIVOT syntax is rejected by Oracle

Any `PIVOT`/`UNPIVOT` transpiled to Oracle fails to run, for two reasons:

```sql
-- ORA-01748: only simple column names allowed here   (qualified FOR/IN columns)
-- ORA-03048: SQL reserved word 'AS' is not ...       (AS before the operator's alias)
... FROM "T" "T" UNPIVOT("REVENUE" FOR "MONTH" IN ("T"."JAN", "T"."FEB")) AS "T"
```

Expected Oracle output: no `AS` before the (UN)PIVOT alias, and the FOR column and the IN-list columns are
written unqualified; the aggregate itself may stay qualified:

- `SELECT * FROM t UNPIVOT(revenue FOR month IN (t.jan, t.feb)) AS u` -> Oracle `SELECT * FROM t UNPIVOT(revenue FOR month IN (jan, feb)) u`
- `SELECT * FROM t PIVOT(SUM(t.val) FOR t.cat IN ('a' AS a)) AS p` -> Oracle `SELECT * FROM t PIVOT(SUM(t.val) FOR cat IN ('a' AS a)) p`
- After the optimizer's `qualify` (which qualifies the IN-list in the AST), Oracle output must still be valid:
  `qualify(parse_one("SELECT * FROM t UNPIVOT(revenue FOR month IN (jan, feb))", dialect="oracle"), schema={"t": {"id": "int", "jan": "int", "feb": "int"}}, dialect="oracle").sql(dialect="oracle")`
  -> `SELECT "T"."ID" AS "ID", "T"."MONTH" AS "MONTH", "T"."REVENUE" AS "REVENUE" FROM "T" "T" UNPIVOT("REVENUE" FOR "MONTH" IN ("JAN", "FEB")) "T"`

Spark already strips qualifiers from pivot fields privately; the same behaviour should be shared rather than
duplicated, and Spark output must not change.''')

G("fresh_sqlglot_8259",
  "Operator-precedence change in the core parser (moving `%` from the additive to the multiplicative tier) "
  "without breaking the two places where `%` means PERCENT (`LIMIT x%`, `TABLESAMPLE (x%)`), which previously "
  "relied on a backtracking hack.",
  r'''`%` (MOD) parses with the wrong precedence

`%` is grouped with `+`/`-` instead of `*`/`/`: `10 % 3 / 2` parses as `10 % (3 / 2)` instead of `(10 % 3) / 2`,
which every SQL engine (DuckDB, Snowflake, Databricks, MySQL, Postgres) evaluates left to right at the same
precedence as `*` and `/`.

Expected:
- `parse_one("SELECT 10 % 3 / 2").expressions[0]` is an `exp.Div` whose `this` is `exp.Mod(10, 3)` and whose
  `expression` is `2`.
- `%` as a percent marker still works: `parse_one("SELECT * FROM range(1000) LIMIT 1 / 10% OFFSET 5")` has a limit
  whose expression is `1 / 10` with `limit_options.percent` set, and offset `5`;
  `parse_one("SELECT * FROM tbl TABLESAMPLE RESERVOIR (20%)")` has a table sample with `percent` = `20` and no `size`.
- No other dialect output changes.''')

G("fresh_sqlglot_8460",
  "Base-generator feature plus Spark generator flag: convert ARRAY_INSERT positions between 0-based and "
  "1-based dialects in both directions (negative indices shift the opposite way, `-1` needs ARRAY_SIZE), "
  "and stop crashing on the raw int offset.",
  r'''ARRAY_INSERT position is not converted between Spark (1-based) and other dialects

Spark's `ARRAY_INSERT` position is 1-based (negative positions count from the end), Snowflake's is 0-based.
Today only the DuckDB generator accounts for this. Other targets either crash or keep the wrong position:

```python
sqlglot.transpile("SELECT ARRAY_INSERT(arr, 1, 'z')", read="spark", write="snowflake")
# ValueError: Unsupported expression type int   (same for presto, bigquery, postgres, default dialect)
sqlglot.transpile("SELECT ARRAY_INSERT(arr, 0, 'z')", read="snowflake", write="spark")
# SELECT ARRAY_INSERT(arr, 0, 'z')   (index 0 errors in Spark; should be 1)
```

Expected (literal positions converted; positive ones shift by the offset, negative ones the other way;
non-literal positions passed through with an "unsupported" warning):

- Spark `SELECT ARRAY_INSERT(ARRAY('a', 'b', 'c'), 1, 'z')` -> Snowflake `SELECT ARRAY_INSERT(['a', 'b', 'c'], 0, 'z')`
  (Spark and Databricks output unchanged).
- Snowflake `SELECT ARRAY_INSERT(['a', 'b', 'c'], -1, 'z')` <-> Spark `SELECT ARRAY_INSERT(ARRAY('a', 'b', 'c'), -2, 'z')` (both directions).
- Spark `SELECT ARRAY_INSERT(ARRAY('a', 'b', 'c'), -1, 'z')` -> Snowflake `SELECT ARRAY_INSERT(['a', 'b', 'c'], ARRAY_SIZE(['a', 'b', 'c']), 'z')`.
- Snowflake `SELECT ARRAY_INSERT(['a', 'b', 'c'], 2, 'z')` -> Spark `SELECT ARRAY_INSERT(ARRAY('a', 'b', 'c'), 3, 'z')`.
- Existing DuckDB ARRAY_INSERT conversions keep working.''')

G("fresh_sqlglot_8471",
  "Tokenizer-level change across four files (shared escape table in tokenizer_core/tokens, MySQL dialect, "
  "SingleStore generator): MySQL-family string escapes must decode `\\0`/`\\Z` as control characters and treat "
  "`\\a`, `\\f`, `\\v` as plain letters, replacing a MySQL-specific mechanism; SingleStore's IS_ASCII pattern must stay valid.",
  r'''MySQL-family string literals decode backslash escapes incorrectly

MySQL (and SingleStore) string literals have their own backslash escapes: `\0` is NUL (ASCII 0) and `\Z` is
ASCII 26 (Ctrl-Z), while `\a`, `\f` and `\v` are NOT escapes in MySQL - a backslash before them just yields the
letter (`'\a'` is `'a'`). sqlglot decodes these differently from MySQL.

Expected (Python string notation below):
- Decoding: in MySQL-family dialects `\0` and `\Z` decode to the control characters, and `\a`, `\f`, `\v`
  decode to the plain letters.
- Generation: a NUL character in a string is written back as backslash-zero for MySQL. ClickHouse
  `"SELECT '\\0'"` -> MySQL `"SELECT '\\0'"` (backslash-zero in the SQL text), not a raw NUL byte.
- SingleStore `IS_ASCII`: the default dialect's `SELECT IS_ASCII('a')` -> SingleStore
  `"SELECT ('a' RLIKE '^[\\0-\x7f]*$')"` (backslash-zero for NUL, a literal DEL character); also inside
  expressions: `SELECT 1 + IS_ASCII('a')` -> `"SELECT 1 + ('a' RLIKE '^[\\0-\x7f]*$')"` and
  `SELECT NOT IS_ASCII('é')` -> `"SELECT NOT ('é' RLIKE '^[\\0-\x7f]*$')"`. SingleStore
  `"SELECT ('a' RLIKE '^[\x00-\x7f]*$')"` (raw NUL character) round-trips to the backslash-zero form.
- The MySQL-specific escape handling should be replaced by the shared tokenizer escape handling rather than
  another special case.''')

G("fresh_sqlglot_8493",
  "T-SQL parser: recognise bare `(NOLOCK)`/`(ROWLOCK)` after a table (and after an alias) as table hints "
  "without breaking derived-table column aliases `AS s(nolock)` or table-valued function calls "
  "`dbo.fn(NOLOCK)`; needs careful lookahead (~65 lines).",
  r'''T-SQL: bare table hints like `FROM dbo.t (NOLOCK)` are parsed as a function call

SQL Server accepts the legacy bare form of table hints, `SELECT * FROM dbo.t (NOLOCK)`, meaning
`WITH (NOLOCK)`. sqlglot parses `dbo.t (NOLOCK)` as a function call, and a bare hint after an alias
(`AS a (NOLOCK)`) as alias columns.

Expected (`read="tsql"`, output via `sql("tsql")`):
- `SELECT * FROM t (NOLOCK)`, `SELECT * FROM dbo.t (NOLOCK)`, `SELECT * FROM [dbo].[t] (NOLOCK)` ->
  `... WITH (NOLOCK)`, and the parsed tree equals that of the `WITH (NOLOCK)` form.
- `SELECT * FROM dbo.t (ROWLOCK)` -> `SELECT * FROM dbo.t WITH (ROWLOCK)`
- `SELECT * FROM dbo.t AS a (NOLOCK)` -> `SELECT * FROM dbo.t AS a WITH (NOLOCK)`; `SELECT * FROM #t (NOLOCK)` ->
  `SELECT * FROM #t WITH (NOLOCK)` (trees equal to the canonical form).
- A table alias directly followed by `(nolock)` on a real table is also a hint:
  `... FROM source_table.dbo.source_data AS s(nolock) CROSS APPLY ...` -> `... FROM source_table.dbo.source_data AS s WITH (NOLOCK) CROSS APPLY ...`
- Unchanged (still column aliases / function calls):
  `SELECT * FROM (SELECT 1 AS x) AS s(nolock)`, `SELECT * FROM (VALUES (1)) AS s(nolock)`,
  `SELECT * FROM dbo.src CROSS APPLY dbo.fn(NOLOCK)`, `SELECT * FROM dbo.src CROSS APPLY dbo.fn(x)`,
  `SELECT * FROM dbo.src CROSS APPLY dbo.fn2(NOLOCK, 1)`, `SELECT * FROM dbo.src CROSS APPLY dbo.fn([NOLOCK])`;
  `SELECT * FROM dbo.fn(1) AS s` -> `SELECT * FROM dbo.FN(1) AS s`, `SELECT * FROM dbo.fn(1)` -> `SELECT * FROM dbo.FN(1)`.''')

G("fresh_sqlglot_8042",
  "Lineage algorithm change (~110 changed lines): fold a whole chain of (UN)PIVOT operators when mapping output "
  "columns to sources, including positional alias-column renames at the end of / inside the chain, multi-value "
  "UNPIVOT, CTE sources, and a guard against folding pivots over different sources.",
  r'''Lineage: columns are not traced through chained (UN)PIVOT operators

`sqlglot.lineage.lineage()` only handles a single pivot per source. When several `PIVOT`/`UNPIVOT` operators are
chained, each one consumes the previous one's output, but lineage reads the mapping off one operator only, so
columns produced by an earlier operator are traced wrongly or not at all.

Expected (`node = lineage(column, sql, schema=..., dialect=...)`, comparing `[d.name for d in node.downstream]`):

1. Snowflake, schema `sales(id, jan, feb, north, south)`:
   `SELECT id, score, headcount FROM sales UNPIVOT(score FOR month IN (jan, feb)) UNPIVOT(headcount FOR region IN (north, south))`
   -> `score`: `["SALES.JAN", "SALES.FEB"]`; `headcount`: `["SALES.NORTH", "SALES.SOUTH"]`; `id`: `["SALES.ID"]`.
2. Through a CTE: `WITH src AS (SELECT id, jan, feb, north, south FROM sales) SELECT score FROM src UNPIVOT(score FOR month IN (jan, feb)) UNPIVOT(headcount FOR region IN (north, south))`
   -> `score`: `["SRC.JAN", "SRC.FEB"]`, and their downstreams are `SALES.JAN` / `SALES.FEB`.
3. Mixed, schema `t(id, cat, val)`: `SELECT id, c, v FROM t PIVOT(SUM(val) FOR cat IN ('a' AS a, 'b' AS b)) UNPIVOT(v FOR c IN (a, b))`
   -> `v`: `["T.VAL", "T.VAL"]`; `id`: `["T.ID"]`.
4. An alias column list renames the chain's final output positionally. Schema `m(empid, dept, jan, feb, n, s)`:
   `SELECT a, b, c, d, e, f FROM m UNPIVOT(sales FOR mon IN (jan, feb)) UNPIVOT(hc FOR reg IN (n, s)) AS t(a, b, c, d, e, f)`
   -> a: `["M.EMPID"]`, b: `["M.DEPT"]`, c and d: `["M.JAN", "M.FEB"]`, e and f: `["M.N", "M.S"]`.
5. An earlier operator's alias list renames passthroughs that a later operator consumes (DuckDB):
   `SELECT hc, region FROM sales UNPIVOT(score FOR month IN (jan, feb)) AS u1(a, b, c, d) UNPIVOT(hc FOR region IN (b, c))`
   -> both `hc` and `region`: `["sales.north", "sales.south"]`. Multi-value form
   `... AS u1(a, b, c, d) UNPIVOT((hi, lo) FOR region IN ((b, c)))` -> `hi`: `["sales.north"]`, `lo`: `["sales.south"]`.
6. Pivots over different sources are not a chain and must not be folded together (degrade to leaves):
   `SELECT s1.val, s2.hc FROM t1 UNPIVOT(val FOR m IN (jan, feb)) AS s1 JOIN t2 UNPIVOT(hc FOR r IN (val, other)) AS s2 ON s1.id = s2.id`
   with `t1(id, jan, feb)`, `t2(id, val, other)` -> `hc`: `["S2.HC"]`, `val`: `["S1.VAL"]`.

Existing single-pivot lineage behaviour must be unchanged.''')
