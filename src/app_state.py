"""Application state container for the NiceGUI UI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.capture.controller import CaptureController
from src.config import settings
from src.indi.client import INDIClient
from src.models.project import (
    CapturePoint,
    Project,
    SplinePath,
)
from src.models.spline import sample_points_along_spline
from src.models.undo import UndoStack


def _default_project() -> Project:
    """Create an empty default project (no control points yet)."""
    return Project(
        project="Untitled",
        path=SplinePath(control_points=[]),
    )


@dataclass
class AppState:
    """Mutable application state shared across UI components.

    Attributes:
        project: The current project data.
        indi_client: INDI client instance (mock by default).
        undo_stack: Undo/redo history stack.
    """

    project: Project = field(default_factory=_default_project)
    indi_client: INDIClient | None = None
    undo_stack: UndoStack = field(default_factory=UndoStack)
    current_mode: str = "pan"
    last_camera: dict[str, float] = field(default_factory=lambda: {
        "canvas_width": 800, "canvas_height": 600,
        "yaw": 0.0, "pitch": 0.0, "fov": 60.0,
        "observer_lat": settings.observer_lat,
        "observer_lon": settings.observer_lon,
        "observer_utc": 0.0,
    })

    def update_capture_points(self) -> None:
        """Re-sample the spline path and rebuild capture points.

        Preserves the status of already-captured points when their
        coordinates match an existing capture point.
        """
        spacing = self.project.capture_settings.point_spacing_deg
        sampled = sample_points_along_spline(
            self.project.path, spacing,
        )
        existing = {
            (cp.ra, cp.dec): cp
            for cp in self.project.capture_points
            if cp.status == "captured"
        }
        points: list[CapturePoint] = []
        for idx, (ra, dec) in enumerate(sampled):
            prev = existing.get((ra, dec))
            if prev is not None:
                points.append(prev.model_copy(update={"index": idx}))
            else:
                points.append(CapturePoint(
                    ra=ra, dec=dec, index=idx,
                ))
        self.project.capture_points = points

    def save_project(self, path: Path) -> None:
        """Serialise the project to a JSON file.

        Args:
            path: Destination file path.
        """
        path.write_text(
            self.project.model_dump_json(indent=2),
        )

    def load_project(self, path: Path) -> None:
        """Load a project from a JSON file.

        Args:
            path: Source file path.
        """
        data = json.loads(path.read_text())
        self.project = Project.model_validate(data)
        self.undo_stack = UndoStack()

    def load_project_from_json(self, json_str: str) -> None:
        """Load a project from a JSON string.

        Replaces the current project and resets undo history.

        Args:
            json_str: JSON-encoded project data.
        """
        self.project = Project.model_validate_json(json_str)
        self.update_capture_points()
        self.undo_stack = UndoStack()

    def start_capture(self) -> CaptureController:
        """Create a CaptureController for the current project.

        Already-captured points are preserved so the controller
        will skip them automatically.

        Returns:
            A ready-to-run CaptureController instance.

        Raises:
            RuntimeError: If no INDI client is connected.
        """
        if self.indi_client is None:
            msg = "No INDI client connected. Use Connect first."
            raise RuntimeError(msg)
        # Ensure capture points are up to date
        self.update_capture_points()
        if len(self.project.capture_points) < 2:
            msg = "Need at least 2 capture points"
            raise RuntimeError(msg)
        output = Path(settings.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        return CaptureController(
            project=self.project,
            indi_client=self.indi_client,
            output_dir=output,
        )
