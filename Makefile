UV ?= uv

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
	@test -n "$(SMOKE_AUDIO)" || (echo "SMOKE_AUDIO 경로를 지정하세요." >&2; exit 2)
	$(UV) run --locked rtzr-stt transcribe "$(SMOKE_AUDIO)" \
		--format all --output-dir build/smoke
