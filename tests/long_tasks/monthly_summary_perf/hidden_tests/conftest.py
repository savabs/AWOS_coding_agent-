"""Hidden acceptance tests for the monthly-summary performance task.

Copied into the finished project as ``hidden_tests/`` and run with the project
root as the working directory.  ``slow_ledger`` is a frozen copy of the
original (slow) implementation, used as the behavioural oracle and as the
speed baseline.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)
