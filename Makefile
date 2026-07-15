UV ?= uv

.PHONY: sync check test lint format smoke-test

sync:
	$(UV) sync --frozen

check:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run pytest

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .

smoke-test:
	@test -n "$(SMOKE_AUDIO)" || (echo "SMOKE_AUDIO 경로를 지정하세요." >&2; exit 2)
	$(UV) run rtzr-stt --env-file ../.env transcribe "$(SMOKE_AUDIO)" \
		--format all --output-dir build/smoke
