import os
import sys

# hidden_tests/ is copied into the project root; make `salesdesk` importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
