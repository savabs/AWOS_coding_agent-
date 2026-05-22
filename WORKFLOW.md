# Daily workflow — Copilot-style, token-owned

This repo is your **self-hosted coding copilot**: same loop as “chat + apply changes in the repo,” except **you** choose the model tier, context budget, and pricing path.

## One command to work

From the **git root** (where `.aider.conf.yml` lives):

```bash
make copilot
```

That starts **Aider** on the **default cheap tier** (DeepSeek-Chat). You describe what you want; Aider proposes edits; you accept or refine.

- **Stronger model when stuck:** `make copilot COPILOT_TIER=4c` (Claude Sonnet + Haiku weak model from this file, with cache prewarm).
- **Gemini fast path:** `make copilot COPILOT_TIER=3g` or `4g`.
- **Thinking / harder logic, still cheap:** `make copilot COPILOT_TIER=3r`.

Equivalent: `make mvp` with `TIER=…` (see `scripts/mvp_aider.py`).

## What you control (vs a black-box copilot)

| Lever | What it does |
|--------|----------------|
| **Tier** (`COPILOT_TIER` / `TIER`) | Which API and model run this session — largest cost lever. |
| **`.aider.conf.yml`** | Repo map size, max chat history, `read:` files (e.g. `CONVENTIONS.md`), diff-based edits, Claude cache. |
| **`.aiderignore`** | What never enters the **repo map** (saves input tokens every turn). |
| **`memories/repo/project_structure.md`** | Canonical project facts — point the agent here instead of pasting essays. |
| **`make report`** | Cost and cache usage from `.aider.llm.history` using `memories/repo/models_pricing_catalog.md`. |

## Token ↔ quality defaults (already tuned)

- **`edit-format: diff`** — model sends **patches**, not whole files → fewer **output** tokens.
- **`map-tokens: 512`** + **`map-refresh: files`** — small map; refreshes when files change.
- **`max-chat-history-tokens: 6144`** — caps rolling chat cost.
- **`read: CONVENTIONS.md`** — one compressed rules file (on Claude, benefits from **prompt cache** when prewarm runs).
- **Tier `2` default** — strong coding value per dollar for routine work; escalate only when quality stalls.

If you mostly use **Gemini** and see parse issues, try `edit-format: diff-fenced` in `.aider.conf.yml` for those sessions (see [Aider edit formats](https://aider.chat/docs/more/edit-formats.html)).

## Copilot-like session pattern

1. **Start:** `make copilot` (or set `COPILOT_TIER` once in your shell profile).
2. **Scope:** Name files or areas (`src/foo.py`, “only change the auth module”).
3. **Iterate:** small asks → review diffs → `/undo` if wrong.
4. **Escalate tier** only after two failed attempts at the same task (see `CONVENTIONS.md` MODEL ROUTING).
5. **End:** `make report` to see spend; optional `python3 scripts/session_checkpoint.py -m "…"` for the next session.

## Requirements

- `.env` with keys for the tiers you use (see `.env.example`).
- `pip install -r requirements.txt` and `aider` on your `PATH`.

## Where this goes next (AWOS)

When the Typer `awos` CLI lands (`docs/specs/awos_mvp_spec.md`), this same loop gains **skeleton/hydrate** context and structured **test–repair** — the **product** grows; **your ownership of tokens** stays explicit.
