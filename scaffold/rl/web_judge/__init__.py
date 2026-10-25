"""WebJudge (Online-Mind2Web's automatic evaluator), ported from OSU-NLP-Group/Online-Mind2Web (MIT, see NOTICE).

    layout.py    judge-layout episodes (result.json + trajectory/), v1 and v2 schemas
    prompts.py   upstream prompts, verbatim
    judge.py     key points -> key screenshots -> outcome; success rule; run orchestration
    backends.py  o4-mini (OpenAI) and WebJudge-7B (OpenAI-compatible vLLM) over urllib
    cost.py      o4-mini prices, image tokens, estimate, hard USD cap
    stats.py     Wilson intervals, exact McNemar

CLI: scripts/web/judge.py. Spec: docs/specs/web_judge.md.
"""
UPSTREAM_REPO = "https://github.com/OSU-NLP-Group/Online-Mind2Web"
UPSTREAM_COMMIT = "f0d805ee0e9e0b3ea70911e45e5264b72968f3dc"
