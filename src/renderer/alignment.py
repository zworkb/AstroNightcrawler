"""Star alignment via astroalign.

Stub — full implementation in Task 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class AlignmentResult:
    """Result of aligning one frame to the reference."""

    frame_index: int
    transform: np.ndarray = field(default_factory=lambda: np.eye(3))
    success: bool = True
