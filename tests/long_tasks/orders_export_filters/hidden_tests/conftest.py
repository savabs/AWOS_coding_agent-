"""Hidden-test setup: make the project root importable.

These tests are copied into ``<project>/hidden_tests/`` and run with the
project root as the working directory.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
