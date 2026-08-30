"""Provider-free live proof for token/context telemetry and CLI inspection."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent.model_call_telemetry import ModelCallTelemetryStore, record_model_call


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store_path = Path(directory)
        record_model_call(
            provider="demo",
            model="demo-model",
            request_type="demo",
            input_tokens=120,
            output_tokens=30,
            input_price=1.0,
            output_price=2.0,
            messages=[
                {"role": "system", "content": "stable instructions"},
                {"role": "user", "content": "TASK: inspect\nCODE: sample"},
                {"role": "assistant", "content": '{"action":"read_file","action_input":{}}'},
                {"role": "user", "content": "OBSERVATION (OK):\nsmall output"},
            ],
            session_id="demo-session",
            task_id="demo-task",
            step=1,
            store=ModelCallTelemetryStore(str(store_path)),
        )
        payload = ModelCallTelemetryStore(str(store_path)).aggregate()
        assert payload["calls"] == 1
        assert payload["total_tokens"] == 150
        print("[TOKEN-PROOF] persisted model call")
        print("[TOKEN-PROOF] aggregate:", json.dumps(payload, sort_keys=True))
        print("[TOKEN-PROOF] CLI: awos tokens --json --store <store>")


if __name__ == "__main__":
    main()
