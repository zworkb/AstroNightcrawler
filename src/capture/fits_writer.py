"""FITS file writer with sequence naming convention."""

from datetime import UTC, datetime
from pathlib import Path

from src.models.project import CapturedFrame, CapturePoint


class FITSWriter:
    """Writes FITS data to an output directory with seq_NNNN_MMM.fits naming."""

    def __init__(self, output_dir: Path) -> None:
        """Create writer. Creates output_dir if it doesn't exist.

        Args:
            output_dir: Directory where FITS files will be written.
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self, point: CapturePoint, exposure_num: int, data: bytes, night: int = 1
    ) -> Path:
        """Write FITS data for a capture point.

        Appends a good CapturedFrame to point.frames, tagged with the
        capture session's night number.
        Returns the full path of the written file.

        Args:
            point: The capture point to write data for.
            exposure_num: The 1-based exposure number.
            data: Raw FITS file bytes to write.
            night: The capture session's night number for this frame.

        Returns:
            The full path of the written file.
        """
        name = point.filename(exposure_num)
        path = self.output_dir / name
        path.write_bytes(data)
        point.frames.append(
            CapturedFrame(
                filename=name,
                status="good",
                night=night,
                captured_at=datetime.now(UTC),
            )
        )
        return path
