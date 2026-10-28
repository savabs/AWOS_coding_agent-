"""Per-repo profiles and per-issue specs for build.py (fresh series, merged >= 2026-08-01).

Accommodations are environmental only (the AWOS venv: Python 3.12.14, pytest 9.1, numpy;
no hypothesis / duckdb / pandas / pytz / dateutil / pytest-cov / pytest-regressions /
PyYAML / scipy). Each is documented in the issue's SOURCE.md.
"""

DROP = [".github", ".gitignore", ".gitattributes", ".gitmodules", ".readthedocs.yaml",
        ".readthedocs.yml", ".pre-commit-config.yaml", ".pre-commit-hooks.yaml",
        ".coveragerc", ".editorconfig", "docs", "doc", "AGENTS.md", "CLAUDE.md",
        "Makefile", "makefile", "MANIFEST.in", "tox.ini", "tox.toml", "uv.lock", "poetry.lock",
        "examples", "CONTRIBUTING.md", "CONTRIBUTING.rst", "SECURITY.md", "mkdocs.yml",
        "RELEASING.md", "release-procedure.md", "changelog.d", "CITATION.cff", "codspeed.yml",
        "bench", "typing_tests", "typing-examples", "requirements-mypy.txt", "scripts", "tools",
        "renovate.json", ".git-blame-ignore-revs", ".devcontainer", ".gitpod.yml",
        "benchmarks", ".cursor", ".claude", "CHANGELOG.md", "codecov.yml", ".codecov.yml",
        "CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.rst", "readthedocs.yaml", ".circleci",
        ".mailmap", "justfile", "Justfile", ".omp"]

# ── sqlglot ──────────────────────────────────────────────────────────────────
OPT_IMPORT = ("import duckdb\nfrom pandas.testing import assert_frame_equal\n",
              "try:\n    import duckdb\n    from pandas.testing import assert_frame_equal\n"
              "except ImportError:  # AWOS accommodation (see tests/awos_stubs.py)\n"
              "    from tests.awos_stubs import assert_frame_equal, duckdb\n")
BQ_IMPORT = ("import pytz\n",
             "try:\n    import pytz\nexcept ImportError:  # AWOS accommodation (see tests/awos_stubs.py)\n"
             "    from tests.awos_stubs import pytz\n")
# environmental failures / cost on the parent, unrelated to any job:
#   test_simplify: 80 date-simplification subtests need python-dateutil (not in the venv)
#   test_convert: needs pytz;  test_lazy_load: spawns a bare `python` (not on the harness PATH)
#   test_max_nodes: ~50 s alone (half the suite's wall time)
#   test_tpch / test_tpcds: 21 subtests whose expected SQL folds DATE + INTERVAL, also dateutil
SQLGLOT_STRIP = {"tests/test_optimizer.py": ["TestOptimizer.test_simplify", "TestOptimizer.test_tpch",
                                             "TestOptimizer.test_tpcds"],
                 "tests/test_expressions.py": ["TestExprs.test_convert"],
                 "tests/dialects/test_dialect.py": ["TestDialect.test_lazy_load"],
                 "tests/test_parser.py": ["TestParser.test_max_nodes"]}

# ── click ────────────────────────────────────────────────────────────────────
CLICK_PP = {"pyproject.toml": [("[tool.pytest.ini_options]\n",
                                "[tool.pytest.ini_options]\npythonpath = [\"src\"]\n"),
                               ('    "error",\n',
                                '    "error",\n    "ignore::pytest.PytestRemovedIn10Warning",\n')]}
CLICK_APPEND = {"tests/conftest.py": (
    "\n\n# AWOS accommodation: click is not installed; let subprocesses import src/\n"
    "import os as _os\n"
    "_src = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), \"src\")\n"
    "_os.environ[\"PYTHONPATH\"] = _os.pathsep.join(\n"
    "    p for p in (_src, _os.environ.get(\"PYTHONPATH\")) if p)\n")}

# ── Python-Markdown ──────────────────────────────────────────────────────────
MD_YAML_SHIM = (
    "import json as _json\n\n\n"
    "class yaml:  # AWOS accommodation: PyYAML is not installed; markdown's CLI falls back to json\n"
    "    dump = staticmethod(_json.dumps)\n"
    "    YAMLError = ValueError\n")
MD_TESTSUITE = [("from unittest import TestSuite\n", "from unittest import TestSuite as _TestSuite\n"),
                ("    suite = TestSuite()\n", "    suite = _TestSuite()\n")]

PYDASH_PYTEST = ("""[tool.pytest.ini_options]
addopts = [
    "--verbose",
    "--doctest-modules",
    "--no-cov-on-fail",
    "--cov-fail-under=100",
    "--cov-report=term-missing",
    "--cov-report=xml:build/coverage/coverage.xml",
    "--cov-report=html:build/coverage",
    "--junitxml=build/testresults/junit.xml",
]
""", """[tool.pytest.ini_options]
pythonpath = ["src"]
addopts = ["--verbose"]
""")

# environmental on every parent (venv pydantic version, macOS tmp symlinks, namespace
# packages visible in the venv's site-packages); fail on the parent and the fix alike
ASTROID_STRIP = {"tests/brain/test_dataclasses.py": ["test_pydantic_field"],
                 "tests/test_get_relative_base_path.py": ["TestModUtilsRelativePath.test_symlink_resolution"],
                 "tests/test_manager.py": ["AstroidManagerTest.test_identify_old_namespace_package_protocol",
                                           "AstroidManagerTest.test_module_is_not_namespace"]}

PROFILES = {
    "sqlglot": dict(
        drop=DROP + ["pdoc", "posts", "sqlglot-integration-tests", "sqlglotc", "sqlglot.png",
                     "tests/test_executor.py"],
        replace={"tests/test_optimizer.py": [OPT_IMPORT],
                 "tests/dialects/test_bigquery.py": [BQ_IMPORT]},
        strip=SQLGLOT_STRIP,
        hidden_strip=[n for v in SQLGLOT_STRIP.values() for n in v],
        hidden_replace=[OPT_IMPORT, BQ_IMPORT],
        write={"sqlglot/_version.py": "__version__ = version = \"0.0.0+awos\"\n"
                                      "__version_tuple__ = version_tuple = (0, 0, 0)\n"},
        copy={"tests/awos_stubs.py": "sqlglot_awos_stubs.py"},
    ),
    "astroid": dict(drop=DROP + ["tests/benchmarks", "script", "tbump.toml", "towncrier.toml",
                                 # the 2026-08 numpy brain predates the venv's NumPy 2.5 (fixed upstream
                                 # later by astroid#3290): ~110 environmental failures on older parents
                                 "tests/brain/numpy"],
                    strip=ASTROID_STRIP, hidden_strip=[n for v in ASTROID_STRIP.values() for n in v]),
    # tests live inside the package; the job's own visible test modules are duplicated
    # into tests/ (spec `write_from`) so the harness's `pytest tests` has a regression suite
    "networkx": dict(drop=DROP + ["requirements"]),
    "click": dict(drop=DROP + ["tests/typing", "tests/test_deprecations.py"],
                  replace=CLICK_PP, append=CLICK_APPEND),
    "attrs": dict(
        drop=DROP + ["tests/strategies.py", "tests/test_3rd_party.py", "tests/test_dunders.py",
                     "tests/test_funcs.py", "tests/test_functional.py", "tests/test_make.py",
                     "tests/test_mypy.yml", "tests/test_pyright.py", "tests/test_packaging.py"],
        replace={"pyproject.toml": [("[tool.pytest.ini_options]\n",
                                     "[tool.pytest.ini_options]\npythonpath = [\"src\"]\n")],
                 "tests/conftest.py": [("from hypothesis import HealthCheck, settings\n", ""),
                                       ("def pytest_configure(config):",
                                        "def _unused_hypothesis_profile(config):")]}),
    "more-itertools": dict(drop=DROP + ["requirements"]),
    "pyparsing": dict(
        # examples/ stays: tests/test_unit.py imports examples.*
        drop=[d for d in DROP if d != "examples"] + ["dest", "run_perf_all_tags.bat", "run_perf_all_tags.sh",
                     "tests/test_diagram.py", "tests/test_examples.py",
                     "update_pyparsing_timestamp.py", "pyparsing_archive.py", "BUILDING.md",
                     "scrutinizer-pyparsing.yml", ".scrutinizer.yml"],
        hidden_replace=[('"tests.test_unit.', '"tests.test_hidden_unit.')],
        # harness hygiene (pytest-timeout is in the venv): an unfixed start state of
        # pyparsing_652 recurses ~forever inside one test; without a per-test cap the
        # hidden run only ends at job_series' 600 s pytest timeout
        write={"pytest.ini": "[pytest]\ntimeout = 120\n"}),
    "markdown": dict(drop=DROP + ["checklinks.sh", ".zensical", ".pyspelling.yml", ".spell-dict"],
                     replace={"tests/test_apis.py": [("import yaml\n", MD_YAML_SHIM)],
                              "tests/test_syntax/extensions/test_md_in_html.py": MD_TESTSUITE},
                     dist_info=True),
    "python-pathspec": dict(drop=DROP + ["dev", "tox-ci-py39.ini", "testpypi_prepublish.py",
                                         "prebuild.py", "benchmarks_backends.md", "DEV.md"]),
    "pydash": dict(drop=DROP + ["tasks.py", "DEVGUIDE.rst", "requirements.txt",
                                "tests/pytest_mypy_testing"],
                   replace={"pyproject.toml": [PYDASH_PYTEST]},
                   strip={"tests/test_functions.py": ["test_debounce", "test_debounce_max_wait",
                                                      "test_throttle", "test_throttle_trailing_call"],
                          "tests/test_utilities.py": ["test_now"]}),
    "boltons": dict(drop=DROP + ["misc", "TODO.rst"]),
    "tinydb": dict(drop=DROP + ["artwork", "mypy.ini"],
                   write={"pytest.ini": "[pytest]\naddopts=--verbose\n"}),
    # pytest-regressions (data_regression fixture) is not in the venv
    "markdown-it-py": dict(drop=DROP + ["benchmarking", "tests/test_tree.py",
                                        "tests/test_port/test_references.py",
                                        "tests/test_api/test_main.py", "tests/test_packaging",
                                        "tests/fuzz", "tests/test_fuzzer.py",
                                        # linkify-it-py not in the venv ("gfm-like2" enables linkify)
                                        "tests/test_linkify.py"],
                           strip={"tests/test_port/test_fixtures.py": ["test_linkify", "test_strikethrough_single_tilde",
                                                                       "test_tasklists", "test_alerts"],
                                  "tests/test_cmark_spec/test_spec.py": ["test_file"]}),
    "pyflakes": dict(drop=DROP + ["bin"]),
    # test_typechecked_disabled_in_optimized_mode spawns `python -O -c "import typeguard"` (not installed)
    "typeguard": dict(drop=DROP + ["tests/mypy", "tests/test_pytest_plugin.py"],
                      strip={"tests/test_typechecked.py": ["test_typechecked_disabled_in_optimized_mode"]},
                      replace={"pyproject.toml": [("[tool.pytest.ini_options]\n",
                                                   "[tool.pytest.ini_options]\npythonpath = [\"src\"]\n")]}),
    "tomlkit": dict(drop=DROP + ["tests/test_toml_tests.py", "tests/toml-test"]),
}


def S(repo, pr, slug, source, tests, **kw):
    return dict(repo=repo, pr=pr, slug=slug, source=source, tests=tests, **kw)


def H(*paths, prefix="tests/"):
    """{post-fix test path: tests/test_hidden_<name>} for each path."""
    out = {}
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        sub = p[len(prefix):].rsplit("/", 1)[0] + "/" if p.startswith(prefix) and "/" in p[len(prefix):] else ""
        out[p] = f"tests/{sub}{name.replace('test_', 'test_hidden_', 1)}"
    return out


SG = "tobymao/sqlglot"
AST = "pylint-dev/astroid"
NX = "networkx/networkx"
SPECS = {
    # ── sqlglot ──────────────────────────────────────────────────────────────
    "fresh_sqlglot_8335": S(SG, 8335, "mysql_index_prefixes", ["sqlglot/parsers/mysql.py"],
                            H("tests/dialects/test_mysql.py")),
    "fresh_sqlglot_8574": S(SG, 8574, "search_cycle_clauses",
                            ["sqlglot/expressions/query.py", "sqlglot/generator.py", "sqlglot/parser.py"],
                            H("tests/dialects/test_oracle.py", "tests/dialects/test_postgres.py")),
    "fresh_sqlglot_8538": S(SG, 8538, "trino_literal_precision",
                            ["sqlglot/parser.py", "sqlglot/parsers/trino.py"],
                            H("tests/dialects/test_trino.py")),
    "fresh_sqlglot_8524": S(SG, 8524, "spark_slice_to_duckdb",
                            ["sqlglot/expressions/array.py", "sqlglot/generators/duckdb.py",
                             "sqlglot/parsers/spark2.py"],
                            H("tests/dialects/test_duckdb.py")),
    "fresh_sqlglot_8029": S(SG, 8029, "oracle_pivot_syntax",
                            ["sqlglot/generator.py", "sqlglot/generators/oracle.py",
                             "sqlglot/generators/spark2.py", "sqlglot/transforms.py"],
                            H("tests/dialects/test_oracle.py")),
    "fresh_sqlglot_8259": S(SG, 8259, "mod_precedence", ["sqlglot/parser.py"],
                            H("tests/test_parser.py")),
    "fresh_sqlglot_8460": S(SG, 8460, "array_insert_position",
                            ["sqlglot/generator.py", "sqlglot/generators/spark.py"],
                            H("tests/dialects/test_spark.py")),
    "fresh_sqlglot_8471": S(SG, 8471, "mysql_backslash_escapes",
                            ["sqlglot/dialects/mysql.py", "sqlglot/generators/singlestore.py",
                             "sqlglot/tokenizer_core.py", "sqlglot/tokens.py"],
                            H("tests/dialects/test_clickhouse.py", "tests/dialects/test_singlestore.py"),
                            # visible copies pin the old raw-NUL output the PR changes (post-fix
                            # versions ship as hidden copies)
                            strip={"tests/dialects/test_clickhouse.py": ["TestClickhouse.test_clickhouse"],
                                   "tests/dialects/test_singlestore.py": ["TestSingleStore.test_string_functions"]}),
    # the visible TestTSQL.test_tsql pins the old `AS s(nolock)` output the PR changes;
    # its post-fix version is in the hidden copy
    "fresh_sqlglot_8493": S(SG, 8493, "tsql_bare_table_hints", ["sqlglot/parsers/tsql.py"],
                            H("tests/dialects/test_tsql.py"),
                            strip={"tests/dialects/test_tsql.py": ["TestTSQL.test_tsql"]}),
    "fresh_sqlglot_8042": S(SG, 8042, "lineage_chained_pivots", ["sqlglot/lineage.py"],
                            H("tests/test_lineage.py")),
    # ── astroid ──────────────────────────────────────────────────────────────
    "fresh_astroid_3348": S(AST, 3348, "self_containing_live_containers",
                            ["astroid/nodes/node_classes.py"],
                            H("tests/test_nodes.py", "tests/test_raw_building.py")),
    "fresh_astroid_3286": S(AST, 3286, "raw_building_parent_cycle", ["astroid/raw_building.py"],
                            H("tests/test_raw_building.py")),
    "fresh_astroid_3235": S(AST, 3235, "fstring_as_string_roundtrip", ["astroid/nodes/as_string.py"],
                            H("tests/test_nodes.py")),
    "fresh_astroid_3130": S(AST, 3130, "cap_percent_formatting",
                            ["astroid/nodes/_base_nodes.py", "astroid/protocols.py"],
                            H("tests/test_inference.py", "tests/test_protocols.py")),
    "fresh_astroid_3193": S(AST, 3193, "stdlib_named_c_extension",
                            ["astroid/manager.py", "astroid/modutils.py"],
                            H("tests/test_manager.py", "tests/test_modutils.py")),
    "fresh_astroid_2977": S(AST, 2977, "and_or_constraints", ["astroid/constraint.py"],
                            H("tests/test_constraint.py")),
    # ── networkx (in-package tests; visible copies written into tests/) ──────
    "fresh_networkx_8892": S(NX, 8892, "weak_graph_in_cached_views",
                             ["networkx/classes/graph.py", "networkx/classes/reportviews.py"],
                             {"networkx/classes/tests/test_graph.py": "tests/test_hidden_graph.py",
                              "networkx/classes/tests/test_reportviews.py": "tests/test_hidden_reportviews.py"},
                             dup={"networkx/classes/tests/test_graph.py": "tests/test_graph.py",
                                  "networkx/classes/tests/test_reportviews.py": "tests/test_reportviews.py"}),
    "fresh_networkx_8837": S(NX, 8837, "digraph_node_connectivity",
                             ["networkx/algorithms/connectivity/connectivity.py",
                              "networkx/algorithms/connectivity/cuts.py"],
                             {"networkx/algorithms/connectivity/tests/test_connectivity.py":
                                  "tests/test_hidden_connectivity.py",
                              "networkx/algorithms/connectivity/tests/test_cuts.py": "tests/test_hidden_cuts.py"},
                             dup={"networkx/algorithms/connectivity/tests/test_connectivity.py":
                                      "tests/test_connectivity.py",
                                  "networkx/algorithms/connectivity/tests/test_cuts.py": "tests/test_cuts.py"}),
    # fresh_networkx_8883 dropped: test_group.py importorskips scipy (not in the venv) -> 0 tests run
    # ── click ────────────────────────────────────────────────────────────────
    "fresh_click_3818": S("pallets/click", 3818, "keyboard_interrupt_race", ["src/click/core.py"],
                          H("tests/test_abort_interrupt.py")),
    "fresh_click_3777": S("pallets/click", 3777, "pager_env_args", ["src/click/_termui_impl.py"],
                          H("tests/test_termui.py")),
    # ── attrs ────────────────────────────────────────────────────────────────
    "fresh_attrs_1592": S("python-attrs/attrs", 1592, "on_setattr_generators",
                          ["src/attr/_compat.py", "src/attr/_make.py", "src/attr/_next_gen.py",
                           "src/attr/setters.py", "src/attrs/__init__.pyi"],
                          H("tests/test_compat.py", "tests/test_setattr.py")),
    # ── more-itertools ───────────────────────────────────────────────────────
    "fresh_more-itertools_1248": S("more-itertools/more-itertools", 1248, "numeric_range_consistency",
                                   ["more_itertools/more.py"], H("tests/test_more.py")),
    "fresh_more-itertools_1261": S("more-itertools/more-itertools", 1261, "combination_with_replacement_index",
                                   ["more_itertools/more.py"], H("tests/test_more.py")),
    # ── pyparsing ────────────────────────────────────────────────────────────
    "fresh_pyparsing_653": S("pyparsing/pyparsing", 653, "match_previous_expr_nested",
                             ["pyparsing/helpers.py"], H("tests/test_unit.py")),
    "fresh_pyparsing_652": S("pyparsing/pyparsing", 652, "bounded_repetition_recursion",
                             ["pyparsing/core.py"], H("tests/test_unit.py")),
    # ── Python-Markdown ──────────────────────────────────────────────────────
    "fresh_markdown_1648": S("Python-Markdown/markdown", 1648, "comment_in_raw_inline_html",
                             ["markdown/extensions/md_in_html.py", "markdown/htmlparser.py"],
                             H("tests/test_syntax/blocks/test_html_blocks.py")),
    # ── pathspec ─────────────────────────────────────────────────────────────
    "fresh_pathspec_157": S("cpburnz/python-pathspec", 157, "bracket_backslash_escapes",
                            ["pathspec/patterns/gitignore/base.py"],
                            H("tests/test_03_gitignore_basic.py", "tests/test_04_gitignore_spec.py"),
                            profile="python-pathspec"),
    "fresh_pathspec_152": S("cpburnz/python-pathspec", 152, "terminal_newline_filename",
                            ["pathspec/_backends/_utils.py", "pathspec/_backends/hyperscan/gitignore.py",
                             "pathspec/_backends/hyperscan/pathspec.py", "pathspec/_backends/re2/gitignore.py",
                             "pathspec/_backends/re2/pathspec.py", "pathspec/patterns/gitignore/basic.py",
                             "pathspec/patterns/gitignore/spec.py"],
                            H("tests/test_03_gitignore_basic.py", "tests/test_07_gitignore_end_anchor.py"),
                            # the visible copy pins the old `$`-anchored regex text the PR changes;
                            # its post-fix version ships as the hidden copy
                            drop=["tests/test_03_gitignore_basic.py"], profile="python-pathspec"),
    "fresh_pathspec_144": S("cpburnz/python-pathspec", 144, "reincluded_ancestor_dir_rule",
                            ["pathspec/_backends/hyperscan/gitignore.py", "pathspec/_backends/re2/gitignore.py",
                             "pathspec/_backends/simple/gitignore.py"],
                            H("tests/test_06_gitignore.py"), profile="python-pathspec"),
    "fresh_pathspec_132": S("cpburnz/python-pathspec", 132, "reinclude_under_excluded_dir",
                            ["pathspec/_backends/hyperscan/gitignore.py", "pathspec/_backends/re2/gitignore.py",
                             "pathspec/_backends/simple/gitignore.py"],
                            H("tests/test_06_gitignore.py"), profile="python-pathspec"),
    # ── pydash / boltons / tinydb ────────────────────────────────────────────
    "fresh_pydash_273": S("dgilland/pydash", 273, "extrema_empty_default",
                          ["src/pydash/helpers.py", "src/pydash/numerical.py"], H("tests/test_numerical.py")),
    "fresh_boltons_513": S("mahmoud/boltons", 513, "spooled_write_and_text_seek",
                           ["boltons/ioutils.py"], H("tests/test_ioutils.py")),
    "fresh_boltons_441": S("mahmoud/boltons", 441, "us_timezone_fold", ["boltons/timeutils.py"],
                           H("tests/test_timeutils.py")),
    "fresh_boltons_443": S("mahmoud/boltons", 443, "edge_case_sweep",
                           ["boltons/iterutils.py", "boltons/jsonutils.py", "boltons/strutils.py",
                            "boltons/tableutils.py", "boltons/timeutils.py"],
                           H("tests/test_iterutils.py", "tests/test_jsonutils.py", "tests/test_strutils.py",
                             "tests/test_tableutils.py", "tests/test_timeutils.py")),
    "fresh_tinydb_634": S("msiemens/tinydb", 634, "caching_middleware_closed",
                          ["tinydb/middlewares.py"], H("tests/test_middlewares.py")),
    # ── markdown-it-py ───────────────────────────────────────────────────────
    "fresh_markdown-it-py_433": S("executablebooks/markdown-it-py", 433, "commonmark_whitespace",
                                  ["markdown_it/common/utils.py", "markdown_it/renderer.py",
                                   "markdown_it/rules_block/heading.py", "markdown_it/rules_block/lheading.py",
                                   "markdown_it/rules_block/paragraph.py", "markdown_it/rules_block/table.py"],
                                  H("tests/test_port/test_whitespace.py")),
    "fresh_markdown-it-py_428": S("executablebooks/markdown-it-py", 428, "unterminated_inline_html",
                                  ["markdown_it/rules_inline/html_inline.py",
                                   "markdown_it/rules_inline/state_inline.py"],
                                  H("tests/test_html_inline.py")),
    # ── pyflakes / typeguard / tomlkit ───────────────────────────────────────
    "fresh_pyflakes_868": S("PyCQA/pyflakes", 868, "typing_special_forms", ["pyflakes/checker.py"],
                            {"pyflakes/test/test_type_annotations.py": "tests/test_hidden_type_annotations.py"},
                            dup={"pyflakes/test/test_type_annotations.py": "tests/test_type_annotations.py"}),
    "fresh_typeguard_571": S("agronholm/typeguard", 571, "typeddict_readonly",
                             ["src/typeguard/_checkers.py"], H("tests/test_checkers.py")),
    "fresh_tomlkit_610": S("python-poetry/tomlkit", 610, "integer_underscores", ["tomlkit/api.py"],
                           H("tests/test_api.py")),
}
