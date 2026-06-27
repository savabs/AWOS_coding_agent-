# AWOS — File Placement Rules

> Loaded into every planner prompt. Overrides generic defaults when present.
> Keep under ~4k characters. The LLM reads this to choose paths.

## Golden rule

**Never** create new deliverables at the repository root. Always use a subfolder.

## Where things go

| User intent | Save to |
|-------------|---------|
| Research / investigate | `docs/research/<slug>.html` (+ thin `.md` stub) |
| Spec / design doc | `docs/specs/<slug>_spec.html` (+ stub) |
| Task checklist | `tasks/active/<slug>.html` (+ stub) |
| Session checkpoint | `docs/memory/checkpoint_YYYY-MM-DD.html` (+ stub) |
| ADR | `docs/adr/NNNN-<slug>.html` (+ stub) |
| Wiki page | `wiki/<slug>.html` (+ stub) |
| Guide / tutorial | `docs/<slug>.md` or `docs/<slug>.html` |
| New Python module | `scaffold/agent/<name>.py` |
| Tests | `tests/test_<module>.py` |

## Naming

- Use `snake_case` slugs: `uncensored_local_agents.html`
- Specs: suffix `_spec.html`
- ADRs: next free `NNNN` in `docs/adr/`

## Examples in this repo

- Research: `docs/research/test_runner_reward.html`
- Spec: `docs/specs/tool_reflection_spec.html`
- Task: `tasks/active/deliverable_router_task.html`
- Guide: `docs/example_uncensored_agent_guide.md`

## Forbidden

- Do **not** write markdown/HTML into `run_awos_demo.py`, `awos.py`, or `setup.py`
- Do **not** put research notes in `docs/` root — use `docs/research/`
- Do **not** put specs in `tasks/` — use `docs/specs/`

## Workflow

Non-trivial feature: **research → spec → task → code** (create files in that order).
