---
name: Clean Code standards
description: Project uses Clean Code Manifesto (cleancode.md) and Python-specific rules (cleancode-python.md) for all coding and reviews
type: reference
---

All code and reviews must follow two documents in the project root:

- `cleancode.md` — language-agnostic Clean Code rules (Robert C. Martin), quantitative metrics, review checkliste
- `cleancode-python.md` — Python-specific: type hints (Pflicht), Google-style docstrings, ruff+mypy tooling, NiceGUI patterns (State in dataclasses, Komponenten-Klassen, @ui.refreshable), Pydantic/dataclass usage

Key thresholds: functions ≤30 lines, files ≤500 lines, ≤3 parameters, nesting ≤3 levels, line length ≤100 chars.