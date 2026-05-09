#!/bin/bash
# Launch the Nightcrawler app with PYTHON_GIL=0 to keep free-threading
# active even when astropy.io.fits._utils tries to re-enable the GIL.
# Without this, the parallel alignment / stretch in pipeline.py silently
# falls back to serialized execution.
#
# Free-threaded build of Python is required (python3.13t).
set -e
export PYTHON_GIL=0
exec uv run python -m src.main "$@"
