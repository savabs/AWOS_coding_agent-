from typing import List, Optional


class TaskQueue:
    """
    Priority task queue. Lower priority number = higher urgency.
    Tasks with the same priority are processed FIFO.
    """

    def __init__(self):
        self._tasks: List[dict] = []
        self._processed: int = 0

    def push(self, name: str, priority: int = 5) -> None:
        """Add a task. Inserts in priority order (ascending)."""
        task = {"name": name, "priority": priority}
        # BUG 1: comparison is reversed — should be < not >
        # This makes high priority (low number) go to the END instead of front
        for i, t in enumerate(self._tasks):
            if priority > t["priority"]:
                self._tasks.insert(i, task)
                return
        self._tasks.append(task)

    def pop(self) -> Optional[dict]:
        """Remove and return the highest-urgency task (lowest priority number)."""
        if not self._tasks:
            return None
        self._processed += 1
        # BUG 2: pops from the end instead of the front
        return self._tasks.pop()

    def peek(self) -> Optional[dict]:
        """Return the next task without removing it."""
        if not self._tasks:
            return None
        return self._tasks[0]

    def pending(self) -> int:
        return len(self._tasks)

    def processed(self) -> int:
        return self._processed

    def drain(self) -> List[dict]:
        """Return all tasks in priority order and clear the queue."""
        # BUG 3: forgets to clear the queue after draining
        result = list(self._tasks)
        return result
