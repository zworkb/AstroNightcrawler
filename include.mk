NC_HOST ?= 0.0.0.0
NC_PORT ?= 8090

.PHONY: run-capturing skydata
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
