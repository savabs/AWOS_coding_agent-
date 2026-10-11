"""dag_executor.max_workers(): env-configurable, cloud default unchanged."""
import os
import unittest
from unittest import mock

from scaffold.agent import dag_executor as d


class MaxWorkers(unittest.TestCase):
    def _w(self, **env):
        keys = ("AWOS_DAG_MAX_WORKERS", "AWOS_PROVIDER", "AWOS_LOCAL_SLOTS")
        clean = {k: v for k, v in os.environ.items() if k not in keys}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return d.max_workers()

    def test_cloud_default(self):
        self.assertEqual(self._w(), 4)

    def test_override(self):
        self.assertEqual(self._w(AWOS_DAG_MAX_WORKERS="8"), 8)
        self.assertEqual(self._w(AWOS_DAG_MAX_WORKERS="8", AWOS_PROVIDER="local"), 8)

    def test_local_follows_slots(self):
        self.assertEqual(self._w(AWOS_PROVIDER="local"), 1)
        self.assertEqual(self._w(AWOS_PROVIDER="local", AWOS_LOCAL_SLOTS="4"), 4)

    def test_bad_values(self):
        self.assertEqual(self._w(AWOS_DAG_MAX_WORKERS="x"), 4)
        self.assertEqual(self._w(AWOS_DAG_MAX_WORKERS="0"), 1)

    def test_wave_uses_cap(self):
        seen = []
        tasks = [{"task_id": f"t{i}", "files": [f"f{i}.py"], "depends_on": []} for i in range(6)]
        import threading, time
        active = {"n": 0, "max": 0}
        lock = threading.Lock()

        def fn(t):
            with lock:
                active["n"] += 1; active["max"] = max(active["max"], active["n"])
            time.sleep(0.05)
            with lock:
                active["n"] -= 1
            seen.append(t["task_id"])
            return {"task_id": t["task_id"], "success": True}

        with mock.patch.dict(os.environ, {"AWOS_DAG_MAX_WORKERS": "2"}):
            d.DAGExecutor(tasks).execute(fn)
        self.assertEqual(len(seen), 6)
        self.assertLessEqual(active["max"], 2)


if __name__ == "__main__":
    unittest.main()
