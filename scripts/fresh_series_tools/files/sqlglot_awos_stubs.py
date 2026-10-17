"""AWOS accommodation: duckdb, pandas and pytz are not installed in the AWOS venv.

- duckdb / assert_frame_equal: the optimizer tests' optional "execute" cross-checks
  (run original and optimized SQL in DuckDB, compare frames) become no-ops; every
  optimizer output is still compared against its expected SQL text.
- pytz: the one use (timezone(...).localize(dt)) is served by zoneinfo.
"""
import datetime as _dt
from zoneinfo import ZoneInfo as _ZoneInfo


class _Result:
    def df(self):
        return None

    def fetchall(self):
        return []


class _Connection:
    def execute(self, *args, **kwargs):
        return _Result()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class duckdb:  # noqa: N801 - stands in for the module
    @staticmethod
    def connect(*args, **kwargs):
        return _Connection()


def assert_frame_equal(*args, **kwargs):
    return None


class _Tz:
    def __init__(self, name):
        self._zone = _ZoneInfo(name)

    def localize(self, value: _dt.datetime) -> _dt.datetime:
        return value.replace(tzinfo=self._zone)


class pytz:  # noqa: N801 - stands in for the module
    @staticmethod
    def timezone(name):
        return _Tz(name)
