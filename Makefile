UV ?= uv
SMOKE_OUTPUT ?= build/fleurs-smoke

.PHONY: sync check test lint format smoke-test

sync:
	$(UV) sync --locked

check:
	$(UV) lock --check
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked pytest

test:
	$(UV) run --locked pytest

lint:
	$(UV) run --locked ruff check .

format:
	$(UV) run --locked ruff format .

smoke-test:
	$(UV) run --locked rtzr-stt evaluate \
		--output-dir "$(SMOKE_OUTPUT)"
