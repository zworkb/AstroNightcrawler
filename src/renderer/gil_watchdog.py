"""Runtime checks for free-threading state during a render.

The renderer relies on ``PYTHON_GIL=0`` plus a free-threaded Python
build (``python3.13t``) to actually parallelise alignment (#117) and
stretch (#118) work. The smoke test in ``scripts/freethread_smoke.py``
already covers import-time detection, but a regression at runtime — for
example, a freshly imported C-extension dependency that requests GIL
re-enable mid-render — would silently destroy the parallelism speedup
with no visible error. This module is the runtime backstop: passive
probes called at known phase boundaries that emit a ``logger.warning``
the first time a stage is observed running with the GIL on.

Design notes:

- ``sys._is_gil_enabled()`` exists on Python 3.13+ and is the same
  probe the CPython test suite uses. It is undocumented but stable
  enough; we wrap it in ``getattr`` and degrade silently on older
  builds.
- We dedupe **per-stage per-render-run**. The render orchestrator is
  expected to call :func:`reset_run_state` at the start of each render,
  then :func:`check_gil_state` at each phase boundary. Without dedup
  the per-frame call sites would flood the log.
- We never crash. Free-threading is a performance feature, not a
  correctness requirement; a warning lets ops investigate without
  blocking the render.

See issue #119.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# Stages already logged during the current render run. Cleared by
# :func:`reset_run_state` so each render starts with a fresh slate.
_warned_for_run: set[str] = set()


def reset_run_state() -> None:
    """Clear the once-per-render-run dedup state.

    Call this at the start of every render so the next run logs the
    GIL state again — otherwise a long-running process would only
    ever log the very first render's state.
    """
    _warned_for_run.clear()


def check_gil_state(stage: str) -> None:
    """Probe ``sys._is_gil_enabled()`` and log once per stage per run.

    Args:
        stage: A short identifier for the call site, e.g.
            ``"render-start"``, ``"alignment-phase"``,
            ``"stretch-phase"``. Used as the dedup key.

    Behaviour:
        - First call for ``stage`` in this run, GIL off → ``logger.info``
        - First call for ``stage`` in this run, GIL on → ``logger.warning``
        - Subsequent calls for the same ``stage`` → no-op
        - Python build without ``sys._is_gil_enabled`` → no-op
    """
    if stage in _warned_for_run:
        return
    _warned_for_run.add(stage)

    is_enabled_fn = getattr(sys, "_is_gil_enabled", None)
    if is_enabled_fn is None:
        # Older Python without the probe — nothing to check. We still
        # marked the stage as visited so we don't re-attempt the
        # ``getattr`` lookup on every call.
        return

    if is_enabled_fn():
        logger.warning(
            "[%s] GIL is enabled — work will run serialised despite "
            "ThreadPoolExecutor. Check that python3.13t is in use and "
            "PYTHON_GIL=0 is set.",
            stage,
        )
    else:
        logger.info("[%s] free-threading active (GIL disabled)", stage)
