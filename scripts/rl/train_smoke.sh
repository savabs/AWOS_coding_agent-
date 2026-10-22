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
  --set max_steps=3 --set grad_accum=2 --set eval_steps=2 --set eval_rows=4 --set preflight_rows=2 --set logging_steps=1 >/dev/null
python3 -m scaffold.rl.train.kaggle_driver push --stage "$W/stage" --dry-run
T1=$(ts)

echo "=== tiny Qwen3.5 + train.py on CPU"
"$TRAIN_PY" -m scaffold.rl.train.tiny_qwen35 --tokenizer "$TOKENIZER" --out "$W/tiny" >/dev/null
AWOS_NO_TORCHRUN=1 "$TRAIN_PY" "$W/stage/kernel/train.py" --config "$W/stage/dataset/train_config.json" \
  --data "$W/stage/dataset" --out "$W/kout" --base-model "$W/tiny" 2>&1 | grep -E "^\[|Error|error" || true
test -f "$W/kout/adapter/adapter_model.safetensors"
python3 -c "import json,sys; m=json.load(open(sys.argv[1])); print({k: m[k] for k in ('preflight_loss','final_loss','global_steps','lora_targets')})" "$W/kout/metrics.json"
T2=$(ts)

echo "=== export"
BASE="$W/tiny" ADAPTER="$W/kout/adapter" OUT="$W/export" NAME=tiny-smoke TRAJ="$TRAJ" \
  bash "$REPO/scripts/rl/train_export.sh" >"$W/export.log" 2>&1 || { tail -40 "$W/export.log"; exit 1; }
python3 -c "import json,sys; s=json.load(open(sys.argv[1])); print('serve_check ok=%s template_equal=%s tokens_equal=%s parsed=%s' % (s['ok'], [x['template_equal'] for x in s['steps']], [x.get('tokens_equal') for x in s['steps']], [bool(x['parsed']) for x in s['steps']]))" "$W/export/serve_check.json"
cat "$W/export/export.json"
echo "SMOKE OK: data+stage=$((T1 - T0))s train=$((T2 - T1))s export=$(( $(ts) - T2 ))s  (work dir $W)"
