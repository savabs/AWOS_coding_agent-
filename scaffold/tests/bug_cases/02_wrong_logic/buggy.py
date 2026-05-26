class Cache:
    """Simple LRU-style cache with a max size."""

    def __init__(self, max_size: int = 10):
        self._store = {}
        self._max_size = max_size

    def get(self, key: str):
        return self._store.get(key)

    def set(self, key: str, value) -> None:
        if key not in self._store and len(self._store) >= self._max_size:
            # BUG: pops an arbitrary key instead of the oldest (insertion-order)
            self._store.pop(next(iter(reversed(list(self._store)))))
        self._store[key] = value

    def size(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store = {}


def is_palindrome(s: str) -> bool:
    """Return True if s is a palindrome (case-insensitive, ignores spaces)."""
    cleaned = s.replace(" ", "").lower()
    # BUG: compares cleaned to original s instead of cleaned reversed
    return cleaned == s
