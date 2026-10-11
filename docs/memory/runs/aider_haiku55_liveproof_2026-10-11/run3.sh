#!/bin/zsh
# Track B1: aider arm on direct Anthropic Haiku 5.5, the 3 pre-registered issues
# the first attempt did not finish. Resumable: an issue whose out dir already
# holds a job_series_*.json is skipped.
W=/Users/becmachlean/Projects/AWOS_coding_agent/.claude/worktrees/wf_db6db965-455-2
PY=/Users/becmachlean/Projects/AWOS_coding_agent/.venv/bin/python
cd $W || exit 1
export PYTHONUNBUFFERED=1 AWOS_SERIES_MODEL=claude-haiku-5-5
for s in real_attrs_1593 real_boltons_428 real_boltons_458; do
  out=b1_runs/$s
  if ls $out/job_series_*.json >/dev/null 2>&1; then echo "skip $s"; continue; fi
  mkdir -p $out
  caffeinate -i $PY scripts/job_series.py run --series $s \
     --series-root $HOME/.awos-harness/real_series \
     --arms aider --out-root $out > b1_runs/$s.log 2>&1
  echo "$s exit=$?"
done
