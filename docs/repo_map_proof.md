# Repo map + grep — live proof

## Scenario

ReAct workers get a pre-computed **repo map** (grep hits + structural anchors + suggested
`read_file` line windows) instead of blind sequential file scrolling. Grep uses **auto-literal**
mode for code symbols containing `.`, `()`, quotes, etc.

## Command

```bash
chmod +x scripts/demo_repo_map.sh
./scripts/demo_repo_map.sh
```

## Success markers

| Marker | Meaning |
|--------|---------|
| `[REPO_MAP] cmd_stats line anchors OK` | Repo map found `cmd_stats` / savings in real `awos.py` |
| `[GREP] literal mode: N hit(s) in awos.py` | Quoted `stats.add_argument("--savings"` grep works without regex error |
| `passed` (pytest) | `tests/test_grep_repo_map.py` green |

## Env

- `AWOS_REPO_MAP=1` (default) — inject map into ReAct task prompt
- `AWOS_EXPLORATION_PHASE=0` — demo isolates repo map from exploration phase

## Checklist

- [ ] Live proof: run `./scripts/demo_repo_map.sh` — watch for `[REPO_MAP]` and `[GREP]` markers
