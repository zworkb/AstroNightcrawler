NC_HOST ?= 0.0.0.0
NC_PORT ?= 8090
SKYDATA_URL ?= https://stellarium-web.org/skydata

.PHONY: run-capture run-render skydata skydata-extra skydata-dso skydata-stars-deep self-heal-venv build-catalog

# Self-heal the venv if its Python build doesn't match $(UV_PYTHON) (#150).
# Runs every invocation, but only deletes when a mismatch is detected. The
# MXENV_TARGET sentinel rule (Makefile, same logic) covers the case where the
# sentinel is stale; this phony target covers the case where the user broke
# the venv manually (e.g. `uv venv -p 3.13`) without invalidating the sentinel.
self-heal-venv:
	@if [ -d .venv ]; then \
		EXISTING=$$(.venv/bin/python -c "import sysconfig; print('t' if sysconfig.get_config_var('Py_GIL_DISABLED') else 'no')" 2>/dev/null || true); \
		WANTED=$$(echo "$(UV_PYTHON)" | grep -qE 't$$' && echo t || echo no); \
		if [ -n "$$EXISTING" ] && [ "$$EXISTING" != "$$WANTED" ]; then \
			echo "WARNING: .venv Python build ($$EXISTING) != UV_PYTHON=$(UV_PYTHON) ($$WANTED) — rebuilding (#150)"; \
			rm -rf .venv .mxmake/sentinels/mxenv.sentinel; \
		fi; \
	fi

# run-render REQUIRES a free-threaded build because it sets PYTHON_GIL=0 for
# parallel alignment / stretch workers. Assert before launching to surface a
# clear repair hint instead of `Fatal Python error: config_read_gil`.
run-render: self-heal-venv install-render .env
	@.venv/bin/python -c "import sysconfig; assert sysconfig.get_config_var('Py_GIL_DISABLED'), \
'.venv ist kein free-threaded Python build. PYTHON_GIL=0 funktioniert nicht. Repair: rm -rf .venv .mxmake/sentinels/mxenv.sentinel && make install-render'"
	PYTHON_GIL=0 .venv/bin/python -c "from src.renderer.cli import main; main(['--ui'])"

# run-capture is Python-build-agnostic: capture has no parallel hot path, so
# it works with BOTH regular and free-threaded Python. No PYTHON_GIL=0, no
# free-threaded assertion — that way the Pi (UV_PYTHON=3.13) and the
# workstation (UV_PYTHON=3.13t) both run capture without any rebuild churn.
run-capture: self-heal-venv install-capture .env skydata
	NC_HOST=$(NC_HOST) NC_PORT=$(NC_PORT) .venv/bin/python -c "from src.main import main; main()"

.env:
	@cp .env.example .env
	@echo "Created .env from .env.example — edit to configure."

skydata:
	@if [ -z "$$(ls -A skydata 2>/dev/null)" ]; then \
		echo "skydata is empty, downloading..."; \
		bash scripts/download_skydata.sh; \
	fi

## Download extended DSO catalogue (Norder 1-3, ~2 MB)
skydata-dso:
	@echo "Downloading extended DSO catalogues..."
	@for norder in 1 2 3; do \
		npix=$$(python3 -c "print(12 * 4**$$norder)"); \
		mkdir -p skydata/dso/Norder$$norder/Dir0; \
		for i in $$(seq 0 $$((npix - 1))); do \
			f="skydata/dso/Norder$$norder/Dir0/Npix$$i.eph"; \
			[ -f "$$f" ] || curl -sfL "$(SKYDATA_URL)/dso/Norder$$norder/Dir0/Npix$$i.eph" -o "$$f" 2>/dev/null || true; \
		done; \
		echo "  DSO Norder$$norder: $$npix tiles"; \
	done
	@echo "DSO catalogues updated."

## Download deeper star catalogues (Norder 2-3, ~20 MB)
skydata-stars-deep:
	@echo "Downloading deep star catalogues..."
	@for norder in 2 3; do \
		npix=$$(python3 -c "print(12 * 4**$$norder)"); \
		mkdir -p skydata/stars/Norder$$norder/Dir0; \
		for i in $$(seq 0 $$((npix - 1))); do \
			f="skydata/stars/Norder$$norder/Dir0/Npix$$i.eph"; \
			[ -f "$$f" ] || curl -sfL "$(SKYDATA_URL)/stars/Norder$$norder/Dir0/Npix$$i.eph" -o "$$f" 2>/dev/null || true; \
		done; \
		echo "  Stars Norder$$norder: $$npix tiles"; \
	done
	@echo "Star catalogues updated."

## Download all extended catalogues (DSO + deep stars)
skydata-extra: skydata-dso skydata-stars-deep
	@du -sh skydata/
	@echo "All extended catalogues downloaded."

## Download large catalogues — full Gaia/DSO (Norder 4-6, ~180 MB, thousands of tiles)
skydata-full: skydata-extra
	@echo "Downloading full star catalogues (this may take a while)..."
	@for norder in 4 5 6; do \
		npix=$$(python3 -c "print(12 * 4**$$norder)"); \
		echo "  Stars Norder$$norder: $$npix tiles..."; \
		for i in $$(seq 0 $$((npix - 1))); do \
			dir="skydata/stars/Norder$$norder/Dir$$(( i / 10000 * 10000 ))"; \
			mkdir -p "$$dir"; \
			f="$$dir/Npix$$i.eph"; \
			[ -f "$$f" ] || curl -sfL "$(SKYDATA_URL)/stars/Norder$$norder/Dir$$(( i / 10000 * 10000 ))/Npix$$i.eph" -o "$$f" 2>/dev/null || true; \
		done; \
	done
	@for norder in 4 5; do \
		npix=$$(python3 -c "print(12 * 4**$$norder)"); \
		echo "  DSO Norder$$norder: $$npix tiles..."; \
		for i in $$(seq 0 $$((npix - 1))); do \
			dir="skydata/dso/Norder$$norder/Dir$$(( i / 10000 * 10000 ))"; \
			mkdir -p "$$dir"; \
			f="$$dir/Npix$$i.eph"; \
			[ -f "$$f" ] || curl -sfL "$(SKYDATA_URL)/dso/Norder$$norder/Dir$$(( i / 10000 * 10000 ))/Npix$$i.eph" -o "$$f" 2>/dev/null || true; \
		done; \
	done
	@du -sh skydata/
	@echo "Full catalogues downloaded."

## Regenerate the bundled catalog (Messier + Caldwell + OpenNGC + IAU named
## stars) at data/catalog.csv from upstream sources. The CSV is committed
## to the repo, so end-users never need to run this — maintainers do it
## ~1-2x/year. See scripts/build_catalog.py for the source list.
build-catalog: install-render
	@.venv/bin/python scripts/build_catalog.py
	@echo "Catalog regenerated at data/catalog.csv"
	@wc -l data/catalog.csv
