"""Tests for frame transition generation."""

import numpy as np

from src.renderer.alignment import AlignmentResult
from src.renderer.transitions import crossfade, linear_pan


class TestCrossfade:
    def test_produces_correct_count(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = list(crossfade(a, b, num_frames=4))
        assert len(frames) == 4

    def test_first_is_mostly_a(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = list(crossfade(a, b, num_frames=4))
        assert frames[0].mean() < 100

    def test_last_is_mostly_b(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = list(crossfade(a, b, num_frames=4))
        assert frames[-1].mean() > 150


class TestLinearPan:
    def test_produces_correct_count(self) -> None:
        a = np.full((100, 100, 3), 128, dtype=np.uint8)
        b = np.full((100, 100, 3), 128, dtype=np.uint8)
        align = AlignmentResult(dx=5.0, dy=3.0, success=True)
        frames = list(linear_pan(a, b, align, num_frames=4, margin_x=5, margin_y=5))
        assert len(frames) == 4

    def test_output_size_is_cropped(self) -> None:
        a = np.full((100, 100, 3), 128, dtype=np.uint8)
        b = np.full((100, 100, 3), 128, dtype=np.uint8)
        align = AlignmentResult(dx=5.0, dy=3.0, success=True)
        frames = list(linear_pan(a, b, align, num_frames=4, margin_x=5, margin_y=5))
        # Crop: 100 - 2*5 = 90 wide, 100 - 2*5 = 90 tall
        assert frames[0].shape == (90, 90, 3)

    def test_zero_rotation_byte_identical_to_legacy_path(self) -> None:
        """Regression guard: rotation == 0 keeps old shift-based output.

        The rotation interpolation added in #125 takes a separate
        ``affine_transform`` code path. We branch on
        ``abs(rotation) >= 1e-6`` so existing renders that have
        translation-only alignments remain byte-identical to pre-#125
        output.
        """
        rng = np.random.default_rng(seed=42)
        a = rng.integers(0, 256, size=(100, 100, 3), dtype=np.uint8)
        b = rng.integers(0, 256, size=(100, 100, 3), dtype=np.uint8)
        # Translation only — rotation defaults to 0.0
        align = AlignmentResult(dx=5.7, dy=3.2, rotation=0.0, success=True)
        frames_zero = list(linear_pan(
            a, b, align, num_frames=4, margin_x=5, margin_y=5,
        ))
        # And again with a rotation just below the threshold — should
        # also take the legacy path and match exactly.
        align_tiny = AlignmentResult(
            dx=5.7, dy=3.2, rotation=1e-7, success=True,
        )
        frames_tiny = list(linear_pan(
            a, b, align_tiny, num_frames=4, margin_x=5, margin_y=5,
        ))
        for f0, ft in zip(frames_zero, frames_tiny):
            np.testing.assert_array_equal(f0, ft)

    def test_zero_blend_tail_byte_identical(self) -> None:
        """Regression guard: ``blend_tail_frames=0`` (default and explicit)
        must produce byte-identical output to the pre-#126 path.

        Existing renders are the workhorse case — anyone who hasn't
        explicitly opted into blending must see exactly the bits they
        saw before. Tested in both code paths (no-rotation fast path
        and rotation affine_transform path).
        """
        rng = np.random.default_rng(seed=123)
        a = rng.integers(0, 256, size=(100, 100, 3), dtype=np.uint8)
        b = rng.integers(0, 256, size=(100, 100, 3), dtype=np.uint8)

        # No-rotation fast path
        align = AlignmentResult(dx=5.7, dy=3.2, rotation=0.0, success=True)
        frames_default = list(linear_pan(
            a, b, align, num_frames=8, margin_x=5, margin_y=5,
        ))
        frames_explicit_zero = list(linear_pan(
            a, b, align, num_frames=8, margin_x=5, margin_y=5,
            blend_tail_frames=0,
        ))
        for f_def, f_zero in zip(
            frames_default, frames_explicit_zero, strict=True,
        ):
            np.testing.assert_array_equal(f_def, f_zero)

        # Rotation path (above the eps threshold)
        align_rot = AlignmentResult(
            dx=5.7, dy=3.2, rotation=2.0, success=True,
        )
        frames_rot_default = list(linear_pan(
            a, b, align_rot, num_frames=8, margin_x=5, margin_y=5,
        ))
        frames_rot_zero = list(linear_pan(
            a, b, align_rot, num_frames=8, margin_x=5, margin_y=5,
            blend_tail_frames=0,
        ))
        for f_def, f_zero in zip(
            frames_rot_default, frames_rot_zero, strict=True,
        ):
            np.testing.assert_array_equal(f_def, f_zero)

    def test_tail_blends_to_frame_b(self) -> None:
        """With uniform-grey frame_a and uniform-white frame_b, the last
        ``blend_tail_frames`` should ramp monotonically from grey to
        white via smoothstep easing.

        Geometry is intentionally trivial (identity alignment, uniform
        colors) so the only thing the test sees is the blend math.
        """
        a = np.full((100, 100, 3), 128, dtype=np.uint8)
        b = np.full((100, 100, 3), 255, dtype=np.uint8)
        align = AlignmentResult(dx=0.0, dy=0.0, rotation=0.0, success=True)

        num_frames = 10
        blend_tail_frames = 4
        frames = list(linear_pan(
            a, b, align,
            num_frames=num_frames,
            margin_x=5, margin_y=5,
            blend_tail_frames=blend_tail_frames,
        ))
        assert len(frames) == num_frames

        means = [float(f.mean()) for f in frames]

        # Pre-blend frames (0 .. num_frames - blend_tail_frames - 1) are
        # pure pan output of frame_a → still grey.
        for m in means[:num_frames - blend_tail_frames]:
            assert abs(m - 128) < 1.0, (
                f"Pre-blend frame mean should be ~128, got {m}"
            )

        # Tail frames must monotonically increase toward white.
        tail_means = means[num_frames - blend_tail_frames:]
        for prev, curr in zip(tail_means, tail_means[1:], strict=False):
            assert curr >= prev, (
                f"Tail brightness should be monotonically non-decreasing, "
                f"got {tail_means}"
            )

        # First tail frame: alpha=0 → still pure grey (128).
        assert abs(tail_means[0] - 128) < 1.0, (
            f"First tail frame should still be grey (alpha=0), "
            f"got mean {tail_means[0]}"
        )
        # Last frame: alpha=1 → pure white (255).
        assert tail_means[-1] > 254.0, (
            f"Last frame should be ~white (alpha=1), got mean {tail_means[-1]}"
        )

    def test_blend_tail_clamped_when_too_large(self) -> None:
        """``blend_tail_frames >= num_frames`` clamps to ``num_frames-1``
        and does not error."""
        a = np.full((50, 50, 3), 128, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        align = AlignmentResult(dx=0.0, dy=0.0, rotation=0.0, success=True)
        # Should not raise; should produce num_frames frames.
        frames = list(linear_pan(
            a, b, align,
            num_frames=4, margin_x=2, margin_y=2,
            blend_tail_frames=10,
        ))
        assert len(frames) == 4

    def test_rotation_interpolates_smoothly(self) -> None:
        """A non-trivial rotation should produce per-frame angular steps.

        We use a small rotation (matches typical astro field-rotation
        magnitudes — sub-degree) so the bright pixel stays inside the
        crop window after rotation around the source origin. astroalign's
        transform is ``a_coord = R @ b_coord + (dy, dx)``, i.e. rotation
        around the source origin (0, 0). The crop window in the output
        therefore moves along an arc as t advances.
        """
        size = 401
        a = np.zeros((size, size, 3), dtype=np.uint8)
        b = np.zeros((size, size, 3), dtype=np.uint8)
        # Bright pixel near crop centre so a small rotation around the
        # source origin (0, 0) keeps it well inside the crop window.
        a[200, 200] = (255, 255, 255)
        b[200, 200] = (255, 255, 255)
        # 2 deg rotation (typical astro magnitude), no translation
        align = AlignmentResult(dx=0.0, dy=0.0, rotation=2.0, success=True)
        frames = list(linear_pan(
            a, b, align, num_frames=7, margin_x=80, margin_y=80,
        ))

        # Track the bright-pixel centroid across frames. Rotation around
        # source origin moves the pixel along an arc; per-frame motion
        # should be smooth and monotonic.
        positions = []
        for f in frames:
            mask = f.sum(axis=2) > 50
            assert mask.any(), "Bright pixel was lost during rotation"
            ys, xs = np.where(mask)
            weights = f[mask].sum(axis=1).astype(np.float64)
            cyf = float(np.average(ys, weights=weights))
            cxf = float(np.average(xs, weights=weights))
            positions.append((cyf, cxf))

        # The pixel should move monotonically in at least one axis as
        # rotation accumulates.
        ys = [p[0] for p in positions]
        xs = [p[1] for p in positions]
        dy_diffs = np.diff(ys)
        dx_diffs = np.diff(xs)
        y_monotonic = np.all(dy_diffs >= -0.5) or np.all(dy_diffs <= 0.5)
        x_monotonic = np.all(dx_diffs >= -0.5) or np.all(dx_diffs <= 0.5)
        assert y_monotonic and x_monotonic, (
            f"Pixel position should change smoothly, got positions {positions}"
        )
        # Per-frame step size should be roughly constant (linear interp).
        step_sizes = [
            np.hypot(dy, dx) for dy, dx in zip(dy_diffs, dx_diffs, strict=True)
        ]
        max_step = max(step_sizes)
        min_step = min(step_sizes)
        assert max_step < 3 * (min_step + 0.5), (
            f"Per-frame steps should be roughly equal, got {step_sizes}"
        )
