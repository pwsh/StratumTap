# StratumTap --- developer tasks
# Everything runs out of a local ./.venv; nothing here touches the deploy target
# except `make deploy`.

PY        ?= python3
VENV      ?= .venv
VPY       := $(VENV)/bin/python
VPIP      := $(VENV)/bin/pip
RUFF      := $(VENV)/bin/ruff
PYTEST    := $(VENV)/bin/pytest
UVICORN   := $(VENV)/bin/uvicorn
PORT      ?= 8080

.DEFAULT_GOAL := help

.PHONY: help venv dev demo run test lint fmt vendor deploy clean

help: ## Show this help
	@echo "StratumTap --- make targets"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VPY):
	$(PY) -m venv $(VENV)
	$(VPIP) install --upgrade pip wheel
	$(VPIP) install -e ".[dev]"

venv: $(VPY) ## Create ./.venv and install the package in editable mode with dev extras

dev: venv ## Run with demo data + auto-reload on http://127.0.0.1:8080/
	STRATUMTAP_DEMO=1 STRATUMTAP_PORT=$(PORT) \
	$(UVICORN) stratumtap.app:create_app --factory --reload \
		--host 127.0.0.1 --port $(PORT)

demo: venv ## Run with synthetic GPS/NTP data (no gpsd or chrony needed)
	STRATUMTAP_DEMO=1 STRATUMTAP_PORT=$(PORT) $(VPY) -m stratumtap

run: venv ## Run against the real local gpsd + chronyc
	STRATUMTAP_PORT=$(PORT) $(VPY) -m stratumtap

test: venv ## Run the test suite
	$(PYTEST) -q

lint: venv ## Lint and check formatting
	$(RUFF) check .
	$(RUFF) format --check .

fmt: venv ## Auto-format and apply safe lint fixes
	$(RUFF) format .
	$(RUFF) check --fix .

vendor: ## Re-vendor the frontend libraries into stratumtap/static/vendor/
	npm install
	npm run vendor

deploy: ## rsync this repo to the target host and run deploy/install.sh there
	bash deploy/deploy.sh

clean: ## Remove build artifacts, caches and the local venv
	rm -rf $(VENV) build dist *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './node_modules/*' -prune -exec rm -rf {} +
