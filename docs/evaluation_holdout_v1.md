---
title: "Held-out Evaluation v1 Operator Contract"
tags:
  - doc/guide
  - phase/1
  - topic/evaluation
---

# Held-out Evaluation v1 Operator Contract

Private holdout tasks must not be committed to this repository or used for prompt/reward tuning. Store them in a separately controlled corpus and publish only metadata manifests and digest-bound report bundles.

## Compare authorized report artifacts

The operator must provide local paths to the manifest and both report bundles. The CLI does not fetch external references or run an agent:

```bash
python3 scripts/verify_holdout_comparison.py \
  --manifest /secure/holdout/manifest.json \
  --baseline /secure/reports/baseline.json \
  --candidate /secure/reports/candidate.json \
  --output /secure/reports/comparison.json
```

Exit status is `0` only when both artifacts verify and no correctness or safety regression is found. Exit status `1` means validation failure or regression. A non-zero regression result is expected evidence, not a CLI malfunction.

## One-command private workspace pipeline

After the private workspace contains `manifest.json`, `reports/baseline/baseline.json`, and `reports/candidate/candidate.json`, run:

```bash
python3 scripts/run_holdout_pipeline.py \
  --root .awos/holdout_private
```

The pipeline validates that every input and output path stays inside the private root, delegates to `verify_holdout_comparison.py`, and writes `comparisons/comparison.json`. It is offline-only: it does not create tasks, copy repositories, call a provider, or fetch remote references.

## Three-task offline smoke proof

To verify the complete runner → report bundle → manifest → comparison flow without a model or provider:

```bash
python3 scripts/run_holdout_smoke.py \
  --root /tmp/awos-holdout-smoke
```

This creates three synthetic tasks, runs deterministic baseline/candidate callbacks, and invokes the real comparison pipeline. It must print `SMOKE_ONLY`; its `regressed=false` result proves plumbing only, not coding-agent capability. The command refuses to overwrite an existing smoke corpus.

The checked-in example manifest and synthetic tests validate contracts only; they are not live capability evidence.

## Related
- [[evaluation_holdout_corpus_v1]]
- [[holdout_report_verification_v1]]
- [[holdout_comparison_cli_v1]]
