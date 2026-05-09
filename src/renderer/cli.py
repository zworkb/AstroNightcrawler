"""CLI entry point for the Nightcrawler renderer."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.config import settings
from src.renderer.debayer import DebayerMode
from src.renderer.pipeline import ProgressUpdate, RenderConfig, RenderPipeline
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
        "--speed", type=float, default=settings.render_speed,
        help="Playback speed multiplier (1=normal, 2=2x faster, 0.5=half)",
    )
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
    p.add_argument(
        "--resolution",
        choices=["native", "4k", "1440p", "1080p", "720p"],
        default=settings.render_resolution,
        help="Output video resolution preset",
    )
    p.add_argument(
        "--workers", "--render-workers", dest="render_workers",
        type=int, default=None,
        help=(
            "Parallel workers for alignment + stretch. -1 = all CPU cores. "
            "Overrides NC_RENDER_WORKERS env var and settings default. "
            "GUI value (when running --ui) still wins over this flag."
        ),
    )
    p.add_argument("--keep-frames", action="store_true", help="Keep intermediate PNGs")
    p.add_argument("--temp-dir", type=Path, default=None, help="Custom temp directory")
    p.add_argument("--ui", action="store_true", help="Start web UI instead of CLI render")
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=settings.log_level,
    )
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

    # Source priority for render_workers:
    #   GUI > CLI flag > NC_RENDER_WORKERS env > settings default(4)
    # The CLI flag is None when not passed, in which case we fall back
    # to settings.render_workers (which already reflects the env var
    # through pydantic_settings). When the CLI is explicit, it overrides.
    workers = (
        args.render_workers
        if args.render_workers is not None
        else settings.render_workers
    )
    return RenderConfig(
        fps=args.fps,
        crf=args.crf,
        stretch_mode=args.stretch,
        stretch_params=stretch_params,
        debayer_mode=debayer_map[args.debayer],
        transition=args.transition,
        crossfade_frames=args.crossfade_frames,
        resolution=args.resolution,
        speed=args.speed,
        keep_frames=args.keep_frames,
        temp_dir=args.temp_dir,
        render_workers=workers,
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv).
    """
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(name)s %(levelname)s: %(message)s",
    )

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

    def _print_progress(update: ProgressUpdate) -> None:
        # Plain stdout line per increment — overwritten in place so the
        # terminal shows a single moving counter per phase rather than
        # spamming N lines. ``\r`` works fine for CI logs (each phase
        # ends naturally when the next phase's first line appears or
        # the render finishes).
        print(  # noqa: T201
            f"\r[{update.phase}] {update.current}/{update.total}  {update.label}",
            end="", flush=True,
        )

    pipeline.render(args.output, on_progress=_print_progress)
    print()  # newline to terminate the in-place progress line  # noqa: T201
    print(f"Video saved to {args.output}")  # noqa: T201


def _start_ui() -> None:
    """Start the renderer web UI."""
    from src.renderer.ui.render_layout import start_render_ui

    start_render_ui()


if __name__ == "__main__":
    main()
