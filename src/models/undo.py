"""Undo/Redo command stack using the memento pattern.

Stores JSON string snapshots of application state, enabling unlimited
undo/redo up to a configurable maximum depth. The stack is agnostic
to the content of the stored strings.
"""

from __future__ import annotations

from collections import deque


class UndoStack:
    """Undo/Redo stack using memento pattern (stores JSON snapshots).

    Attributes:
        max_size: Maximum number of entries kept in the undo history.
    """

    def __init__(self, max_size: int = 50) -> None:
        """Initialise the undo stack.

        Args:
            max_size: Maximum number of undo entries to retain.
                When exceeded, the oldest entries are dropped.
        """
        self.max_size = max_size
        self._undo: deque[str] = deque(maxlen=max_size)
        self._redo: deque[str] = deque()

    def push(self, before_state: str, after_state: str) -> None:
        """Record a state change, saving *before_state* for undo.

        Any pending redo history is cleared, since the timeline has
        diverged.

        Args:
            before_state: The serialised state before the change.
            after_state: The serialised state after the change (kept
                internally so redo can restore it).
        """
        self._undo.append(before_state)
        self._redo.clear()

    def undo(self) -> str | None:
        """Pop the most recent undo entry and return the state to restore.

        The popped state is moved to the redo stack so it can be
        re-applied later.

        Returns:
            The JSON string to restore, or ``None`` if nothing to undo.
        """
        if not self._undo:
            return None
        state = self._undo.pop()
        self._redo.append(state)
        return state

    def redo(self) -> str | None:
        """Pop the most recent redo entry and return the state to restore.

        The popped state is moved back to the undo stack.

        Returns:
            The JSON string to restore, or ``None`` if nothing to redo.
        """
        if not self._redo:
            return None
        state = self._redo.pop()
        self._undo.append(state)
        return state

    @property
    def can_undo(self) -> bool:
        """Whether there are entries available to undo."""
        return len(self._undo) > 0

    @property
    def can_redo(self) -> bool:
        """Whether there are entries available to redo."""
        return len(self._redo) > 0
