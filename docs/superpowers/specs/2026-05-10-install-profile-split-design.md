# Install Profile Split — Design

Epic: [#6 Code Quality](https://github.com/zworkb/AstroNightcrawler/issues/6)
Status: design approved, ready for implementation plan

## 1. Goal

Make `make install` aware of two deployment profiles so the **capture machine** (typically a Raspberry Pi running INDI + the planner UI) doesn't have to pull in heavy render-only dependencies (`scipy`, `astroalign`, `colour-demosaicing`, `Pillow`). The rendering workstation continues to install the full stack.

## 2. Why now

Renderer-only packages add ~150-250 MB of disk and several minutes of install time. The Raspi only ever runs capture and the planner UI — it has neither the CPU nor the use case for `scipy.ndimage.shift`, `astroalign`, or Bayer demosaicing. The asymmetry was implicit; this spec makes it explicit and self-documenting.

This is also the natural anchor for any future architectural split (Approach D from the brainstorm: headless capture, separate planner UI on workstation). That stays out of scope here.

## 3. Dependency classification

| Dependency | Profile |
|---|---|
| `nicegui` | both — UI on both sides (the planner UI lives on the Raspi too) |
| `fastapi`, `uvicorn` | both — ASGI server on both sides |
| `pydantic`, `pydantic-settings` | both — Project model + Settings |
| `astropy` | both — capture writes FITS, renderer reads them |
| `numpy` | both — Project model arithmetic, FITS data |
| `scipy` | **render only** — `ndimage.shift` for linear-pan transitions |
| `astroalign` | **render only** — star-triangle alignment |
| `colour-demosaicing` | **render only** — Bayer-pattern → RGB |
| `Pillow` | **render only** — label drawing, PNG frame export, preview JPEGs |

Four packages move to `[project.optional-dependencies.render]`. Nothing moves to a `capture` extra — the pure-Python async INDI client introduced in #39 means the capture side has no extra runtime deps beyond the shared core.

## 4. `pyproject.toml`

```toml
[project]
name = "nightcrawler"
version = "0.1.0"
description = "Telescope imaging sequence planner and capture controller"
requires-python = ">=3.11"
dependencies = [
    "nicegui>=2.0",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "astropy>=6.0",
    "numpy>=1.26",
]

[project.optional-dependencies]
render = [
    "scipy>=1.11",
    "astroalign>=2.6",
    "colour-demosaicing>=0.2",
    "Pillow>=10.0",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
    "mypy>=1.10",
    "nightcrawler[render]",   # dev/CI tests both sides
]

[project.scripts]
nightcrawler = "src.main:main"
nightcrawler-render = "src.renderer.cli:main"
```

The `nightcrawler[render]` self-reference inside the `dev` extra is standard PEP 621 syntax and pulls the render extras transitively.

## 5. Make targets

Three explicit targets — no auto-detection magic.

| Target | What it does | Used on |
|---|---|---|
| `make install` | Unchanged from today: full mxmake chain (ruff/black/mypy/pytest/pyrefly/ty/etc.) + render + dev extras | dev workstations, CI |
| `make install-capture` | `uv pip install -e .` only. No render extras, no QA tools | Raspberry Pi |
| `make install-render` | `uv pip install -e ".[render]"`. No QA tools, no dev | rendering workstation |

The profile targets deliberately bypass the mxmake QA toolchain (ruff/black/isort/mypy/pyrefly/ty/coverage/pyupgrade). Those tools are for developing the codebase, not for running it; production deployments don't need them.

### Implementation outline

In the Makefile (the post-SETTINGS section can be edited; mxmake regenerates only the SETTINGS block above the `END SETTINGS` marker):

```makefile
.PHONY: install-capture
install-capture: $(MXENV_TARGET)
	@echo "Installing capture profile (core deps only)"
	@$(MXENV_PYTHON) -m uv pip install -e .

.PHONY: install-render
install-render: $(MXENV_TARGET)
	@echo "Installing render profile (core + render extras)"
	@$(MXENV_PYTHON) -m uv pip install -e ".[render]"
```

(Exact target wiring depends on the current Makefile structure; the implementing agent verifies `MXENV_TARGET` and `MXENV_PYTHON` are the right hooks.)

## 6. Python version handling per machine

Orthogonal to dependency splitting, but worth pinning down so the implementation doesn't accidentally entangle them.

- The current `.python-version` is `3.13t` (free-threaded). That stays — it's the right default for the rendering workstation.
- On the Raspberry Pi, the user overrides via `UV_PYTHON=3.13 make install-capture`. Documented in the README installation section.
- If this turns out to be annoying in practice, a follow-up issue can add per-profile Python version selection (e.g., `make install-capture` implies `UV_PYTHON=3.13`). Not in scope now.

## 7. Import discipline

The split is enforced by the package manager (capture-side install simply doesn't have `scipy`/`astroalign`/`colour-demosaicing`/`Pillow`). If capture-side code accidentally imports something from `src/renderer/`, it crashes at import time with a clear `ModuleNotFoundError`.

No lint rule, no static check. The behaviour is intentional: fail loud at startup if someone wires the wrong thing.

The shared boundary is `src/models/` — that module must stay importable on the capture side, so it cannot import from `src/renderer/` or use render-only deps. (Today it doesn't; this is a maintenance constraint.)

## 8. README update

Add an "Installation" section with three sub-sections:

```markdown
## Installation

### Capture machine (Raspberry Pi)
The capture and planner UI runs on the machine that controls the
telescope. It needs the core dependencies only.

    UV_PYTHON=3.13 make install-capture

The planner UI is then available at `http://<raspi-ip>:8090`.

### Rendering workstation
The renderer runs on a workstation. It needs the render extras
(scipy, astroalign, colour-demosaicing, Pillow). Free-threaded
Python 3.13t is recommended to escape the GIL during alignment.

    make install-render
    PYTHON_GIL=0 nightcrawler-render --ui

### Development
For development and CI, install everything:

    make install

This includes all profiles, the QA toolchain (ruff, mypy, etc.),
and the test suite.
```

## 9. Acceptance criteria

For the implementation to be considered done:

1. `make install` still produces the same fully-equipped environment as today (149+ existing tests pass).
2. On a fresh workstation venv, `make install-render` produces an environment that can run `nightcrawler-render --ui` and complete a real render.
3. On a fresh capture venv (no render extras), `python -c "from src.main import main"` succeeds — i.e. the capture/planner entry point doesn't import any render-only module.
4. `python -c "from src.renderer.cli import main"` on a capture-only venv fails with a clear `ModuleNotFoundError` mentioning one of the render-only packages.
5. `README.md` documents the three install paths.

## 10. Out of scope

- Splitting into two PyPI packages (`nightcrawler-capture` + `nightcrawler-render`). Approach D from the brainstorm; bigger refactor, deferred.
- Auto-detecting profile by `uname -m`. Explicit > magical.
- Lint rule enforcing the cross-module import discipline. Trust the package manager and clear errors instead.
- Per-profile Python version selection in the Makefile. Add later if the manual `UV_PYTHON=3.13` override gets annoying.
- A `capture` extra. No capture-only PyPI deps exist today.
