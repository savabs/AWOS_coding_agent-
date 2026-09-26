import os


def resolve(path):
    """Turn a user-supplied path into an absolute one."""
    return os.path.abspath(path)
