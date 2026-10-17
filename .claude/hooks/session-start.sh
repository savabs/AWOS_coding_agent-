#!/bin/bash
# Cloud sessions (claude.ai/code) start from a fresh clone with no .venv.
# Build it so `.venv/bin/python -m pytest ...` (docs/HANDOFF.md) works at once.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

# aider-chat needs Python < 3.13 (the cloud image has 3.13), so it is skipped
# here; the loop executor and the test suite don't need it.
grep -v -i '^aider-chat' requirements.txt > /tmp/awos-requirements-cloud.txt
.venv/bin/pip install -q --disable-pip-version-check -r /tmp/awos-requirements-cloud.txt pytest ruff

# No ANTHROPIC_API_KEY here on purpose: model calls go through GitHub Actions.
echo 'export PYTHONDONTWRITEBYTECODE=1' >> "${CLAUDE_ENV_FILE:-/dev/null}"
