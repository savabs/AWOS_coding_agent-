from pathutils import normalize_path


def resolve(path):
    """Turn a user-supplied path into an absolute one."""
    return normalize_path(path)
