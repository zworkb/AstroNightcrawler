NC_HOST ?= 0.0.0.0
NC_PORT ?= 8090
SKYDATA_URL ?= https://stellarium-web.org/skydata

.PHONY: run-capturing skydata skydata-extra skydata-dso skydata-stars-deep
run-capturing: $(INSTALL_TARGETS) .env skydata
	NC_HOST=$(NC_HOST) NC_PORT=$(NC_PORT) python -c "from src.main import main; main()"

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
