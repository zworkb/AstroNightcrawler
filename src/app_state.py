"""Application state container for the NiceGUI UI."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.capture.controller import CaptureController
from src.config import settings
from src.indi.client import INDIClient
from src.models.project import (
    CapturePoint,
    Project,
    ReconcileReport,
    SplinePath,
)
from src.models.spline import sample_points_along_spline
from src.models.undo import UndoStack


def _restore_from_manifest(project: Project, manifest_path: Path) -> None:
    """Restore captured state from an existing manifest.

    Routes the saved manifest through ``Project.model_validate_json`` so the
    legacy migration validator and datetime parsing run automatically, then
    merges captured frames into the current plan by point index. Points that
    were complete (or skipped) in the previous session keep their frames so
    the controller skips them.
    """
    saved = Project.model_validate_json(manifest_path.read_text())
    saved_by_index = {sp.index: sp for sp in saved.capture_points}

    for point in project.capture_points:
        sp = saved_by_index.get(point.index)
        if sp is None:
            continue
        if sp.skipped:
            point.skipped = True
            point.status = "skipped"
            continue
        if sp.frames:
            point.frames = [f.model_copy(deep=True) for f in sp.frames]
            point.target_subs = sp.target_subs
            point.captured_at = sp.captured_at
            if point.is_complete:
                point.status = "captured"

    captured = sum(1 for p in project.capture_points if p.is_complete)
    logging.getLogger("capture").info(
        "Resumed from manifest: %d/%d points already complete",
        captured, len(project.capture_points),
    )


def _resolve_output_dir(
    project: Project, opened_dir: Path | None = None,
) -> Path:
    """Build output directory: base_dir / sequence_name.

    When *opened_dir* is set (a project was opened via ``open_project``
    or freshly created via ``new_project``), capture writes back into
    that exact directory so a subsequent run continues the same project
    in place — no resume/counter logic is needed because the state is
    already loaded.

    Otherwise uses the sequence name from capture settings, or
    auto-generates one from the current datetime. If the directory
    already contains a manifest.json, resumes from the previous capture
    session. Otherwise, appends a counter if the directory already exists.

    Note: the datetime/counter fallback path only kicks in for transient
    un-located projects (``project_dir is None``). Once ``new_project``
    or ``open_project`` has bound a directory, the *opened_dir* branch
    above runs instead. Removing the fallback entirely (so every
    capture requires an explicitly located project) is tracked in #142.

    Args:
        project: The project containing capture settings.
        opened_dir: Directory of a project opened via ``open_project``,
            or None for a brand-new project.

    Returns:
        Path to the created output directory.
    """
    if opened_dir is not None:
        opened_dir.mkdir(parents=True, exist_ok=True)
        return opened_dir

    base = Path(settings.output_dir)
    seq_name = project.capture_settings.sequence_name.strip()
    if not seq_name:
        seq_name = datetime.now().strftime("%Y-%m-%d_%H%M")

    output = base / seq_name

    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        # Resume: load manifest and restore point statuses
        _restore_from_manifest(project, manifest_path)
        return output

    if output.exists():
        # Directory exists but no manifest — append counter
        counter = 2
        while (base / f"{seq_name}_{counter}").exists():
            counter += 1
        output = base / f"{seq_name}_{counter}"

    output.mkdir(parents=True, exist_ok=True)
    return output


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
    project_dir: Path | None = None
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

        Preserves already-complete (or skipped) points when their
        coordinates match an existing capture point.
        """
        spacing = self.project.capture_settings.point_spacing_deg
        sampled = sample_points_along_spline(
            self.project.path, spacing,
        )
        existing = {
            (cp.ra, cp.dec): cp
            for cp in self.project.capture_points
            if cp.is_complete
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
        # Loading a plain plan starts a new project, not a bound resume.
        self.project_dir = None

    def new_project(self, parent_dir: Path, name: str) -> Path:
        """Create ``parent_dir/name`` as a fresh, located project.

        Writes an initial ``manifest.json`` (``indent=2`` to match
        ``CaptureController._save_manifest``), binds ``project_dir`` and
        resets the undo stack so the project is ready to draw into from
        the very first interaction. Symmetric to ``open_project``.

        Args:
            parent_dir: Directory to create the project folder inside.
            name: Project folder name (also the project's display name).
                Whitespace is stripped; path separators are rejected.

        Returns:
            The created project directory path.

        Raises:
            ValueError: If *name* is empty/whitespace-only or contains
                a path separator (``/`` or ``\\``).
            FileExistsError: If ``parent_dir/name`` already exists (we
                refuse to overwrite an existing directory silently).
        """
        clean = (name or "").strip()
        if not clean:
            msg = "Project name must not be empty"
            raise ValueError(msg)
        if "/" in clean or "\\" in clean:
            msg = f"Project name must not contain path separators: {clean!r}"
            raise ValueError(msg)

        target = parent_dir / clean
        if target.exists():
            msg = f"Directory already exists: {target}"
            raise FileExistsError(msg)

        target.mkdir(parents=True)
        self.project = Project(
            project=clean,
            path=SplinePath(control_points=[]),
        )
        (target / "manifest.json").write_text(
            self.project.model_dump_json(indent=2),
        )
        self.project_dir = target
        self.undo_stack = UndoStack()

        logging.getLogger("capture").info(
            "Created new project %r at %s", clean, target,
        )
        return target

    def open_project(self, project_dir: Path) -> ReconcileReport:
        """Open an existing capture project from its directory.

        Loads ``manifest.json`` (migrating legacy schema via the model
        validator), reconciles the frame records against the FITS files
        actually present on disk, and binds the capture output to this
        directory so a subsequent run continues the same project.

        Unlike ``load_project_from_json`` this does NOT re-sample the
        spline (``update_capture_points``): the manifest's capture_points
        are authoritative for an opened project. Re-sampling could drift
        the coordinates and silently drop the loaded frames (which are
        preserved only by exact (ra, dec) match), so we keep them as-is.

        Args:
            project_dir: Directory containing ``manifest.json`` + FITS files.

        Returns:
            The reconcile report (removed frame count + affected points).

        Raises:
            FileNotFoundError: If no ``manifest.json`` exists in *project_dir*.
        """
        manifest_path = project_dir / "manifest.json"
        if not manifest_path.exists():
            msg = f"No manifest.json found in {project_dir}"
            raise FileNotFoundError(msg)

        self.project = Project.model_validate_json(manifest_path.read_text())
        report = self.project.reconcile_with_disk(project_dir)
        self.project_dir = project_dir
        self.undo_stack = UndoStack()

        complete = sum(1 for p in self.project.capture_points if p.is_complete)
        log = logging.getLogger("capture")
        log.info(
            "Opened project %r: %d/%d points complete",
            self.project.project, complete, len(self.project.capture_points),
        )
        if report.removed_count:
            log.info(
                "%d frame(s) missing on disk — will be re-captured (points %s)",
                report.removed_count, report.affected_points,
            )
        return report

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
        # For an opened project the manifest's capture_points are
        # authoritative (and carry the loaded frames), so we must NOT
        # re-sample the spline here — that could drift coords and drop
        # captured frames. Only re-sample for in-app planning.
        if self.project_dir is None:
            self.update_capture_points()
        if len(self.project.capture_points) < 2:
            msg = "Need at least 2 capture points"
            raise RuntimeError(msg)
        # Don't reset points that are already complete (from manifest resume).
        # Reset incomplete, non-skipped points to a clean pending state and
        # set how many good subs we want per point from the capture settings.
        target = self.project.capture_settings.exposures_per_point
        for pt in self.project.capture_points:
            if pt.skipped or pt.is_complete:
                continue
            pt.status = "pending"
            pt.frames = []
            pt.captured_at = None
            pt.target_subs = target
        output = _resolve_output_dir(self.project, self.project_dir)
        # Tag this session's frames with the next night number: one past the
        # highest night already recorded, so a multi-night resume keeps each
        # session's frames distinguishable. A fresh project starts at night 1.
        night = self.project.next_night()
        return CaptureController(
            project=self.project,
            indi_client=self.indi_client,
            output_dir=output,
            night=night,
        )
