"""CLI entry point for the Nightcrawler renderer."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.config import settings
from src.renderer.debayer import DebayerMode
from src.renderer.pipeline import RenderConfig, RenderPipeline
from src.renderer.stretch import StretchParams


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv).

    Returns:
        Parsed namespace with all CLI flags.
    """
    p = argparse.ArgumentParser(
        prog="nightcrawler-render",
        description="Render captured FITS sequences to video.",
    )
    p.add_argument("--input", "-i", type=Path, default=None, help="Capture directory")
    p.add_argument("--output", "-o", type=Path, default=Path("output.mp4"), help="Output video")
    p.add_argument("--fps", type=int, default=settings.render_fps)
    p.add_argument("--crf", type=int, default=settings.render_crf)
    p.add_argument(
        "--stretch", choices=["auto", "histogram", "manual"], default="auto",
    )
    p.add_argument("--black", type=float, default=0.0, help="Manual black point")
    p.add_argument("--white", type=float, default=1.0, help="Manual white point")
    p.add_argument("--midtone", type=float, default=0.5, help="Manual midtone")
    p.add_argument(
        "--transition",
        choices=["none", "crossfade", "linear-pan"],
        default=settings.render_transition,
    )
    p.add_argument(
        "--crossfade-frames", type=int, default=settings.render_crossfade_frames,
    )
    p.add_argument(
        "--debayer",
        choices=["auto", "off", "RGGB", "GBRG", "GRBG", "BGGR"],
        default="auto",
    )
    p.add_argument("--keep-frames", action="store_true", help="Keep intermediate PNGs")
    p.add_argument("--temp-dir", type=Path, default=None, help="Custom temp directory")
    p.add_argument("--ui", action="store_true", help="Start web UI instead of CLI render")
    return p.parse_args(argv)


def _build_config(args: argparse.Namespace) -> RenderConfig:
    """Build a RenderConfig from parsed CLI arguments.

    Args:
        args: Parsed argparse namespace.

    Returns:
        Configured RenderConfig instance.
    """
    debayer_map = {
        "auto": DebayerMode.AUTO,
        "off": DebayerMode.OFF,
        "RGGB": DebayerMode.RGGB,
        "GBRG": DebayerMode.GBRG,
        "GRBG": DebayerMode.GRBG,
        "BGGR": DebayerMode.BGGR,
    }

    stretch_params = None
    if args.stretch == "manual":
        stretch_params = StretchParams(
            black=args.black, white=args.white, midtone=args.midtone,
        )

    return RenderConfig(
        fps=args.fps,
        crf=args.crf,
        stretch_mode=args.stretch,
        stretch_params=stretch_params,
        debayer_mode=debayer_map[args.debayer],
        transition=args.transition,
        crossfade_frames=args.crossfade_frames,
        keep_frames=args.keep_frames,
        temp_dir=args.temp_dir,
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(name)s %(levelname)s: %(message)s",
    )
    args = parse_args(argv)

    if args.ui:
        _start_ui()
        return

    if not args.input:
        print("Error: --input is required (or use --ui for web interface)")  # noqa: T201
        raise SystemExit(1)

    config = _build_config(args)
    pipeline = RenderPipeline(args.input, config)
    pipeline.load()
    print(f"Loaded {len(pipeline.frames)} frames from {args.input}")  # noqa: T201
    pipeline.render(args.output)
    print(f"Video saved to {args.output}")  # noqa: T201


def _start_ui() -> None:
    """Start the renderer web UI."""
    from src.renderer.ui.render_layout import start_render_ui

    start_render_ui()


if __name__ == "__main__":
    main()
