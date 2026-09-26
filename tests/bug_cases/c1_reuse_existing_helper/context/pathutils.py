"""Shared path handling. Use these rather than calling os.path directly."""

import os


def normalize_path(path):
    """Expand ~, collapse .. segments, and return an absolute path."""
    return os.path.abspath(os.path.expanduser(path))
