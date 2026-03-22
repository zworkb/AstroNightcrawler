"""Tests for the UndoStack memento-based undo/redo system."""

from __future__ import annotations

from src.models.undo import UndoStack


class TestUndoStack:
    """Unit tests for UndoStack."""

    def test_push_and_undo_restores_previous_state(self) -> None:
        """Pushing a change then undoing returns the before-state."""
        stack = UndoStack()
        stack.push('{"v":1}', '{"v":2}')
        result = stack.undo()
        assert result == '{"v":1}'

    def test_redo_reapplies_after_undo(self) -> None:
        """After an undo, redo returns the state that was undone."""
        stack = UndoStack()
        stack.push('{"v":1}', '{"v":2}')
        undone = stack.undo()
        assert undone == '{"v":1}'
        redone = stack.redo()
        assert redone == '{"v":1}'

    def test_undo_on_empty_stack_returns_none(self) -> None:
        """Undo with no history returns None."""
        stack = UndoStack()
        assert stack.undo() is None

    def test_redo_on_empty_stack_returns_none(self) -> None:
        """Redo with no redo history returns None."""
        stack = UndoStack()
        assert stack.redo() is None

    def test_new_push_after_undo_clears_redo(self) -> None:
        """A new push after undo discards the redo history."""
        stack = UndoStack()
        stack.push('{"v":1}', '{"v":2}')
        stack.push('{"v":2}', '{"v":3}')
        stack.undo()
        assert stack.can_redo
        stack.push('{"v":2}', '{"v":4}')
        assert not stack.can_redo
        assert stack.redo() is None

    def test_max_size_is_respected(self) -> None:
        """Pushing beyond max_size drops the oldest entries."""
        stack = UndoStack(max_size=50)
        for i in range(60):
            stack.push(f'{{"v":{i}}}', f'{{"v":{i + 1}}}')
        # Count how many undos we can perform
        count = 0
        while stack.undo() is not None:
            count += 1
        assert count == 50

    def test_can_undo_reflects_state(self) -> None:
        """can_undo is False when empty, True after push, False after full undo."""
        stack = UndoStack()
        assert not stack.can_undo
        stack.push('{"a":1}', '{"a":2}')
        assert stack.can_undo
        stack.undo()
        assert not stack.can_undo

    def test_can_redo_reflects_state(self) -> None:
        """can_redo is False initially, True after undo, False after redo."""
        stack = UndoStack()
        assert not stack.can_redo
        stack.push('{"a":1}', '{"a":2}')
        assert not stack.can_redo
        stack.undo()
        assert stack.can_redo
        stack.redo()
        assert not stack.can_redo

    def test_multiple_undo_redo_cycle(self) -> None:
        """Multiple push/undo/redo operations maintain correct order."""
        stack = UndoStack()
        stack.push("s0", "s1")
        stack.push("s1", "s2")
        stack.push("s2", "s3")
        assert stack.undo() == "s2"
        assert stack.undo() == "s1"
        assert stack.redo() == "s1"
        assert stack.redo() == "s2"
