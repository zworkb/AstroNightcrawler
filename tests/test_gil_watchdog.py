"""Tests for the GIL runtime watchdog (#119)."""

from __future__ import annotations

import logging
from unittest.mock import patch

from src.renderer import gil_watchdog


class TestGilWatchdog:
    def setup_method(self) -> None:
        # Each test gets a clean dedup state — reuses prod helper.
        gil_watchdog.reset_run_state()

    def test_warning_when_gil_enabled(self, caplog) -> None:
        """If sys._is_gil_enabled() returns True, a WARNING is emitted."""
        caplog.set_level(logging.WARNING, logger="src.renderer.gil_watchdog")
        with patch("src.renderer.gil_watchdog.sys") as mock_sys:
            mock_sys._is_gil_enabled = lambda: True
            gil_watchdog.check_gil_state("test-stage")
        assert any(
            r.levelno == logging.WARNING
            and "GIL is enabled" in r.getMessage()
            and "test-stage" in r.getMessage()
            for r in caplog.records
        ), f"expected WARNING with 'GIL is enabled', got: {caplog.records!r}"

    def test_info_when_gil_disabled(self, caplog) -> None:
        """If sys._is_gil_enabled() returns False, an INFO is emitted (no warning)."""
        caplog.set_level(logging.INFO, logger="src.renderer.gil_watchdog")
        with patch("src.renderer.gil_watchdog.sys") as mock_sys:
            mock_sys._is_gil_enabled = lambda: False
            gil_watchdog.check_gil_state("test-stage")
        assert any(
            r.levelno == logging.INFO
            and "free-threading active" in r.getMessage()
            for r in caplog.records
        )
        assert not any(r.levelno == logging.WARNING for r in caplog.records)

    def test_dedup_per_stage(self, caplog) -> None:
        """Repeated calls for the same stage emit only one log record."""
        caplog.set_level(logging.WARNING, logger="src.renderer.gil_watchdog")
        with patch("src.renderer.gil_watchdog.sys") as mock_sys:
            mock_sys._is_gil_enabled = lambda: True
            for _ in range(5):
                gil_watchdog.check_gil_state("same-stage")
        warning_count = sum(
            1 for r in caplog.records if r.levelno == logging.WARNING
        )
        assert warning_count == 1, f"expected 1 warning, got {warning_count}"

    def test_dedup_separate_stages(self, caplog) -> None:
        """Different stage names each get their own (one) log record."""
        caplog.set_level(logging.WARNING, logger="src.renderer.gil_watchdog")
        with patch("src.renderer.gil_watchdog.sys") as mock_sys:
            mock_sys._is_gil_enabled = lambda: True
            gil_watchdog.check_gil_state("stage-a")
            gil_watchdog.check_gil_state("stage-b")
            gil_watchdog.check_gil_state("stage-a")  # dedup
        warning_count = sum(
            1 for r in caplog.records if r.levelno == logging.WARNING
        )
        assert warning_count == 2

    def test_reset_run_state_clears_dedup(self, caplog) -> None:
        """After reset_run_state, the same stage logs again."""
        caplog.set_level(logging.WARNING, logger="src.renderer.gil_watchdog")
        with patch("src.renderer.gil_watchdog.sys") as mock_sys:
            mock_sys._is_gil_enabled = lambda: True
            gil_watchdog.check_gil_state("stage-x")
            gil_watchdog.reset_run_state()
            gil_watchdog.check_gil_state("stage-x")
        warning_count = sum(
            1 for r in caplog.records if r.levelno == logging.WARNING
        )
        assert warning_count == 2

    def test_no_attribute_error_on_missing_probe(self, caplog) -> None:
        """On Python builds without sys._is_gil_enabled, the call is a no-op
        (no exception, no log)."""
        caplog.set_level(logging.DEBUG, logger="src.renderer.gil_watchdog")

        class FakeSys:
            pass  # no _is_gil_enabled attribute

        with patch("src.renderer.gil_watchdog.sys", FakeSys()):
            gil_watchdog.check_gil_state("legacy-stage")
        # Either zero records, or none mentioning the stage as an
        # alert — silent fallback is acceptable.
        relevant = [r for r in caplog.records if "legacy-stage" in r.getMessage()]
        assert relevant == []
