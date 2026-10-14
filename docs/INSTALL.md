# Installing AWOS

AWOS runs coding jobs on your own repo. Each job runs in a throwaway git
worktree, and the result comes back as a branch you review (`awos/<id>`).
AWOS never edits your checkout.

## Requirements

- macOS or Linux (the job host uses `fcntl` file locks, so Windows is not supported)
- Python 3.11 or newer
- `git` on `PATH`
- One model provider:
  - **Anthropic** (recommended): an `ANTHROPIC_API_KEY`. With only this key set,
    AWOS uses `claude-haiku-5-5` for everything. A small bug fix costs about $0.002.
  - **Local**: any OpenAI-compatible server (llama.cpp `llama-server`, Ollama,
    LM Studio, …). No key is needed and runs cost $0.

## Install

One line (macOS or Linux):

```bash
curl -fsSL https://awos.tirramind.com/install.sh | sh
```

Then run `awos`. The first time, it asks you to pick a model provider: an
Anthropic key, which it checks with a free request and saves in
`~/.awos/.env` with mode 600, or a local model server. It also finds your
repo's test command. You can run `awos doctor` whenever you like: it checks
everything and prints one fix line for each problem.

What the installer does:

- If `uv` is on your PATH, it runs `uv tool install`. uv provides Python 3.11+
  if you don't have it.
- Otherwise, if you have `pipx` and Python 3.11+, it uses `pipx`.
- Otherwise, it tells you what it's about to do and installs uv with uv's
  official installer (`https://astral.sh/uv/install.sh`). uv goes into
  `~/.local/bin` and no shell rc files are changed. It then uses uv.

It never uses `sudo` and never edits your shell startup files. If `awos` ends
up in a directory that isn't on your PATH, it prints the `export PATH=...` line
for you to add. Run it again to reinstall or upgrade in place.

Options (environment variables):

| Variable | Effect |
|----------|--------|
| `AWOS_REF=<tag/branch/sha>` | Install a different ref (default `v0.2.2`) |
| `AWOS_INSTALL_DRY_RUN=1` | Print every action instead of doing it |
| `AWOS_NO_UV_INSTALL=1` | Never install uv; fail with instructions instead |

To see what it would do first:
`curl -fsSL https://awos.tirramind.com/install.sh | less`, or
`curl -fsSL https://awos.tirramind.com/install.sh | AWOS_INSTALL_DRY_RUN=1 sh`.

### Alternatives

With uv:

```bash
uv tool install --python ">=3.11" "awos @ git+https://github.com/savabs/AWOS_coding_agent-@v0.2.2"
```

With pipx:

```bash
pipx install "git+https://github.com/savabs/AWOS_coding_agent-@v0.2.2"
```

Or with pip, into a virtualenv:

```bash
python3 -m venv ~/.venvs/awos
~/.venvs/awos/bin/pip install "git+https://github.com/savabs/AWOS_coding_agent-@v0.2.2"
export PATH="$HOME/.venvs/awos/bin:$PATH"
```

From a local clone: `pip install /path/to/AWOS_coding_agent-`, or use `pip install -e .`
to work on AWOS itself.

To install a branch, replace `@v0.2.2` with `@<branch>`.

### Optional extras

The core install includes only what the quickstart needs: `anthropic`, `openai`,
`python-dotenv`, `numpy`, `requests` and `pytest`. Heavier features are extras:

| Extra | Adds |
|-------|------|
| `treesitter` | Tree-sitter repo maps and symbol index |
| `memory` | Vector memory (`awos memory`, `awos index`): chromadb, fastembed |
| `tools` | Web search/fetch, GitHub and Hugging Face tools |
| `gemini` | Gemini planner backend |
| `all` | Everything above, plus `mcp` |

For example: `pipx install "awos[treesitter] @ git+https://github.com/savabs/AWOS_coding_agent-@v0.2.2"`.

## Quickstart (about 2 minutes)

Run this from any git repo that has a failing test:

```bash
awos doctor                                # checklist with fix lines; spends nothing
export ANTHROPIC_API_KEY=sk-ant-...        # or let `awos` ask for it, or see "Local model"
awos keys                                  # checks credentials, spends nothing
awos submit --repo . --budget 0.50 "fix average() so the tests pass"
awos serve --worker --once                 # runs the queue, then exits
awos status --digest                       # what happened, tests, spend
git diff main...awos/<id>                  # review the result (id from submit/digest)
git merge awos/<id>                        # if you like it
```

`awos submit` prints the job id. The digest and `awos status <id>` show the
branch name (`awos/<first 8 chars of id>`), the host's own pytest result and
the cost.

When you run `awos keys` without a provider, it exits 1 and tells you exactly
what to set. `awos serve --worker` refuses to start without a provider.

## Local model

```bash
export AWOS_PROVIDER=local
export AWOS_LOCAL_BASE_URL=http://127.0.0.1:8080/v1   # default; any OpenAI-compatible server
export AWOS_LOCAL_MODEL=qwen3.5-9b                    # the name your server serves
awos keys                                             # "local  ok" once the server answers
```

Use `--privacy local_only` on `awos submit` to blank every cloud key for that
job. The job then fails closed instead of reaching a cloud provider.

## Where things live

| Path | What |
|------|------|
| `~/.awos/.env` | Optional key file. Exported variables always win over it |
| `~/.awos/host/` | Job queue, per-job logs, reports, digest (`AWOS_HOST_DIR`) |
| `~/.awos/runtime/.awos/` | Spend ledger (`budget.json`), learning state, traces |

Set `AWOS_HOME` to move all of this, or `AWOS_STATE_DIR` to move only the
runtime dir. Nothing is written inside the installed package. A source checkout
(`python awos.py ...`) keeps its old layout, with `<repo>/.env` and `<repo>/.awos/`.

## Choosing models

If `ANTHROPIC_API_KEY` is set and you haven't set `AWOS_PROVIDER`,
`AWOS_AGENT_MODEL`, `AWOS_PLANNER_MODEL`, `AWOS_NOTEBOOK_MODEL` or `AWOS_LADDER`,
AWOS sets all four model variables to `claude-haiku-5-5`. If you set any of
them yourself, AWOS changes none of them.

## Uninstall

```bash
uv tool uninstall awos         # or: pipx uninstall awos / pip uninstall awos
rm -rf ~/.awos                 # queue, reports and spend ledger (optional)
git branch -D awos/<id>        # result branches, in each repo you used (optional)
```

## Known limits of the installed CLI

The quickstart commands are `keys`, `submit --repo`, `serve --worker`,
`status`, `cancel` and `unblock`. Some other commands still expect a source
checkout:

- `awos serve` (web dashboard) and `awos observe` / `control` use the `gui/` package
- `awos gauntlet` and `awos mission` use `scripts/`
- `awos memory` and `awos index` need the `memory` extra
- The host verifies a job by running `pytest` with AWOS's own Python. Repos
  whose tests import third-party packages need those packages installed in the
  same environment (`pipx inject awos <pkg>`).
