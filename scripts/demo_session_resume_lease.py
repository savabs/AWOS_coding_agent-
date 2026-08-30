"""Provider-free proof that concurrent session resume admission is exclusive."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent.runtime_session import (
    RuntimeSessionStore,
    SessionResumeError,
    SessionStatus,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = RuntimeSessionStore(str(Path(directory) / "sessions"))
        session = store.create("resume lease proof")
        store.finalize(session, SessionStatus.PAUSED)
        lease = store._lease_path(session.session_id)
        lease.write_text(
            json.dumps({"pid": os.getpid(), "session_id": session.session_id}),
            encoding="utf-8",
        )
        print("LEASE_ACQUIRED")
        try:
            store.begin_resume(session.session_id)
        except SessionResumeError:
            print("CONTENTION_REJECTED")
        else:
            raise AssertionError("live lease did not reject a concurrent resume")
        lease.unlink()
        resumed = store.begin_resume(session.session_id)
        assert resumed.status == SessionStatus.RUNNING
        print("LEASE_RELEASED")


if __name__ == "__main__":
    main()
