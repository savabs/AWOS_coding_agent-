# ReAct Worker — Live Proof Guide

## What this proves

The P0 agent capability stack from `docs/research/agent_capability_gap_analysis_2026-06.md`:

| Component | Module | Marker |
|-----------|--------|--------|
| Pre-plan exploration | `scaffold/agent/exploration_phase.py` | `[EXPLORE] Pre-plan repository scan...` |
| Workspace tools | `scaffold/agent/coding_tools.py` | pytest `test_edit_file_*` pass |
| ReAct loop | `scaffold/agent/react_worker.py` | `[PROOF] ReAct mock loop: grep → edit_file → finish OK` |
| Web research | `web_search` + `fetch_url` in `coding_tools.py` | `web_search` in tool catalog / pytest |
| GitHub / HF / Python | `github_*`, `hf_*`, `run_python` in registry | pytest `test_github_search_code_dispatchable` |

## Full tool surface (ReAct worker)

| Category | Tools |
|----------|-------|
| Local | `read_file`, `write_file`, `edit_file`, `grep`, `find_files`, `list_dir`, `shell`, `run_tests` |
| Web | `web_search`, `fetch_url` |
| GitHub | `github_search_repos`, `github_read_file`, `github_list_issues`, `github_search_code` |
| HuggingFace | `hf_search_models`, `hf_search_datasets`, `hf_model_info`, `hf_download_file` |
| Code probe | `run_python` |
| Done | `finish` |

`github_create_issue` only when `AWOS_ALLOW_GITHUB_WRITE=1`.
| Orchestrator wiring | `scaffold/agent/orchestrator.py` | `[TASK N] ReAct worker (...)` on real runs |

## Quick proof (no API keys)

```bash
bash scripts/demo_react_worker.sh
```

### Success criteria

1. Exploration prints keyword grep summary
2. All `tests/test_react_worker.py` tests pass
3. Console shows: `[PROOF] ReAct mock loop: grep → edit_file → finish OK`

## Full proof (real LLM + tests)

```bash
export AWOS_REACT_WORKER=1          # default ON
export AWOS_EXPLORATION_PHASE=1     # default ON
export AWOS_SAFE_TO_RUN_TESTS=1
export AWOS_RUNTIME_SESSION=1

awos worker start --auto-approve "Add a one-line docstring to scaffold/agent/react_worker.py module"
```

### Success markers

- `[EXPLORE] N grep hit(s) across M file(s)`
- `[TASK 1] ReAct worker (DeepSeek V4 Flash)` or similar model
- `[REACT] Turn 1/N` … tool actions … `finish`
- `[TASK 1] REACT OK: ...`
- `[TASK 1] Tests: X passed, 0 failed` (when tests enabled)

## Disable ReAct (legacy patch worker)

```bash
export AWOS_REACT_WORKER=0
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AWOS_REACT_WORKER` | `1` | Use ReAct tool loop instead of single-shot SEARCH/REPLACE |
| `AWOS_EXPLORATION_PHASE` | `1` | Grep/find before planning |
| `AWOS_REACT_MAX_TURNS` | `20` | Max tool turns per task |
| `AWOS_SAFE_TO_RUN_TESTS` | unset | Must be `1` for `run_tests` tool and post-task gate |
| `AWOS_ENABLE_WEB_TOOLS` | `1` | Register `web_search` + `fetch_url` on ReAct worker |
| `AWOS_ENABLE_GITHUB_TOOLS` | `1` | Register GitHub read/search tools |
| `AWOS_ALLOW_GITHUB_WRITE` | unset | Set `1` to enable `github_create_issue` |
| `AWOS_ENABLE_HF_TOOLS` | `1` | Register HuggingFace Hub tools |
| `AWOS_ENABLE_CODE_RUNNER` | `1` | Expose `run_python` (exec needs `ALLOW_CODE_EXEC=1`) |
| `GITHUB_TOKEN` | optional | Higher GitHub API rate limits |
| `HF_TOKEN` | optional | Private HF repos / downloads |
| `TAVILY_API_KEY` | optional | Primary web search backend |

## Task checklist

- [x] Live proof: run `bash scripts/demo_react_worker.sh` — watch for `[PROOF] ReAct mock loop`
