#!/usr/bin/env bash
# CPU end-to-end smoke of the Stage-A pipeline with a TINY random Qwen3.5 (no GPU, no Kaggle account):
#   trajectories -> dataset.py -> kaggle_driver stage + push --dry-run -> kaggle/rl-sft/train.py (CPU,
#   3 steps, LoRA incl. GatedDeltaNet projections) -> merge_lora -> convert_hf_to_gguf b11146 ->
#   llama-quantize Q4_K_M -> llama-server serve_check (template/tokens equal, grammar reply parses).
# ~1-2 min; safe on battery. Used by .github/workflows/rl-export.yml (mode=smoke).
#
#   TRAJ=runs/rl/demos_factory_train/trajectories.jsonl TOKENIZER=<dir with Qwen3.5 tokenizer files> \
#   TRAIN_PY=<python with torch+transformers 5.19+peft> PY=<llama.cpp convert python> \
#   LLAMA_SRC=<llama.cpp b11146 tree> bash scripts/rl/train_smoke.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
: "${TRAJ:?}" "${TOKENIZER:?}" "${TRAIN_PY:?}" "${PY:?}" "${LLAMA_SRC:?}"
W="${WORK:-$(mktemp -d)}"; mkdir -p "$W"
ts() { date +%s; }
T0=$(ts)

echo "=== dataset"
python3 -m scaffold.rl.train.dataset --input "oracle=$TRAJ" --out "$W/data" --val-frac 0.1 --max-examples 24 | tail -3
echo "=== kaggle stage + push --dry-run"
python3 -m scaffold.rl.train.kaggle_driver stage --data "$W/data" --out "$W/stage" --size 2B --user smoke \
  --set max_steps=3 --set grad_accum=2 --set eval_steps=2 --set eval_rows=4 --set preflight_rows=2 --set logging_steps=1 \
  --set eval_gen_rows=4 --set stop_check_steps=1 --set gpu_sample_s=5 --set 'ntfy_topic=""' --set save_steps=2 --set mem_probe_steps=2 --set mem_trace=true >/dev/null
python3 -m scaffold.rl.train.kaggle_driver push --stage "$W/stage" --dry-run
T1=$(ts)

echo "=== tiny Qwen3.5 + train.py on CPU"
"$TRAIN_PY" -m scaffold.rl.train.tiny_qwen35 --tokenizer "$TOKENIZER" --out "$W/tiny" >/dev/null
# right-padding (pad_multiple) must not change the loss: causal model, loss stops at the real length
"$TRAIN_PY" - "$W/tiny" "$W/stage/kernel/train.py" <<'PY'
import importlib.util, sys, torch
spec = importlib.util.spec_from_file_location("t", sys.argv[2]); T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
from transformers import AutoModelForImageTextToText
m = AutoModelForImageTextToText.from_pretrained(sys.argv[1], dtype=torch.float32).eval()
ids = list(range(5, 5 + 37)); pl = 20
with torch.no_grad():
    a = T.completion_loss(m, torch.tensor([ids]), pl).item()
    b = T.completion_loss(m, torch.tensor([T.pad_ids(ids, 16, 0)]), pl, len(ids)).item()
    la, ka = T.completion_logps(m, torch.tensor([ids]), pl)
    lb, kb = T.completion_logps(m, torch.tensor([T.pad_ids(ids, 16, 0)]), pl, len(ids))
assert abs(a - b) < 1e-4 and abs(la.item() - lb.item()) < 1e-3 and ka == kb, (a, b, la, lb, ka, kb)
print(f"padding check: loss {a:.5f} == padded {b:.5f}, completion tokens {ka}")
PY
AWOS_NO_TORCHRUN=1 "$TRAIN_PY" "$W/stage/kernel/train.py" --config "$W/stage/dataset/train_config.json" \
  --data "$W/stage/dataset" --out "$W/kout" --base-model "$W/tiny" 2>&1 | grep -E "^\[|Error|error" || true
test -f "$W/kout/adapter/adapter_model.safetensors"
python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print({k: m[k] for k in ('preflight_loss','final_loss','global_steps','lora_targets','final_action_accuracy','stopped_by')})" "$W/kout/metrics.json"
# progress rows: one per log step, and an eval row carrying the action accuracy
python3 -c "
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1])]
ev = [r for r in rows if r['kind'] == 'eval']
assert rows and ev and 'action_acc' in ev[-1] and ev[-1]['total'] == 3, rows
print('progress rows', len(rows), 'last eval', {k: ev[-1].get(k) for k in ('step', 'eval_loss', 'action_acc')})
" "$W/kout/progress.jsonl"
# memory probe rows (every 2 steps): smaps split, tensor census, tracemalloc growth lines
python3 -c "
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1])]
r = rows[-1]
assert r['step'] == 2 and r['mem'].get('anon_gb') is not None and r['tensors'].get('cpu', {}).get('n', 0) > 0, r
assert r.get('py_growth') is not None, r
print('mem probe', {k: r[k] for k in ('step', 'mem', 'tensors')}, r['py_growth'][:2])
" "$W/kout/mem_rank0.jsonl"

echo "=== resume: exact (same data, continue from checkpoint-2 to step 5) and warm (new run from the adapter)"
mkcfg() { python3 -c "
import json, sys
c = json.load(open(sys.argv[1])); c.update(json.loads(sys.argv[2])); json.dump(c, open(sys.argv[3], 'w'))
" "$W/stage/dataset/train_config.json" "$1" "$2"; }
mkcfg "{\"max_steps\": 5, \"resume_from\": \"$W/kout/checkpoints\", \"resume_mode\": \"exact\"}" "$W/cfg_exact.json"
AWOS_NO_TORCHRUN=1 "$TRAIN_PY" "$W/stage/kernel/train.py" --config "$W/cfg_exact.json" --data "$W/stage/dataset" \
  --out "$W/kout_exact" --base-model "$W/tiny" 2>&1 | grep -E "resume:|done|Error|error" || true
python3 -c "import json,sys; m=json.load(open(sys.argv[1])); assert m['global_steps'] == 5, m['global_steps']; print('exact resume ok: reached step', m['global_steps'])" "$W/kout_exact/metrics.json"
mkcfg "{\"max_steps\": 2, \"resume_from\": \"$W/kout/adapter\", \"resume_mode\": \"warm\"}" "$W/cfg_warm.json"
AWOS_NO_TORCHRUN=1 "$TRAIN_PY" "$W/stage/kernel/train.py" --config "$W/cfg_warm.json" --data "$W/stage/dataset" \
  --out "$W/kout_warm" --base-model "$W/tiny" 2>&1 | grep -E "resume:|done|Error|error" || true
grep -q "resume: warm" "$W/kout_warm/train_stdout.log" 2>/dev/null || python3 -c "import json,sys; m=json.load(open(sys.argv[1])); assert m['global_steps'] == 2; print('warm start ok: new run of', m['global_steps'], 'steps')" "$W/kout_warm/metrics.json"
T2=$(ts)

echo "=== export"
BASE="$W/tiny" ADAPTER="$W/kout/adapter" OUT="$W/export" NAME=tiny-smoke TRAJ="$TRAJ" \
  bash "$REPO/scripts/rl/train_export.sh" >"$W/export.log" 2>&1 || { tail -40 "$W/export.log"; exit 1; }
python3 -c "import json,sys; s=json.load(open(sys.argv[1])); print('serve_check ok=%s template_equal=%s tokens_equal=%s parsed=%s' % (s['ok'], [x['template_equal'] for x in s['steps']], [x.get('tokens_equal') for x in s['steps']], [bool(x['parsed']) for x in s['steps']]))" "$W/export/serve_check.json"
cat "$W/export/export.json"
echo "SMOKE OK: data+stage=$((T1 - T0))s train=$((T2 - T1))s export=$(( $(ts) - T2 ))s  (work dir $W)"
