# Punchwalk make targets. See CLAUDE.md "Stack" for the agreed entry points.
SHELL := /bin/bash
.DEFAULT_GOAL := help
WEB_EXISTS := $(wildcard web/package.json)

# Fail with a readable message when a target's script has not been written yet.
define require
	@if [ ! -e "$(1)" ]; then echo "not implemented yet: $(1)"; exit 2; fi
endef

.PHONY: help setup dev test lint eval eval-live probe record seed build screenshots docker deploy

help:
	@echo "targets: setup dev test lint eval eval-live probe record seed build screenshots docker deploy"

setup:
	uv sync
ifneq ($(WEB_EXISTS),)
	pnpm -C web install
endif

dev:
	$(call require,punchwalk/api/app.py)
	uv run uvicorn punchwalk.api.app:app --reload --port 8000 --no-proxy-headers

test:
	PUNCHWALK_CACHE=readonly uv run pytest -q
ifneq ($(WEB_EXISTS),)
	pnpm -C web typecheck && pnpm -C web test
endif

lint:
	uv run ruff check .

eval:
	$(call require,eval/run_eval.py)
	uv run python eval/run_eval.py

eval-live:
	$(call require,eval/run_eval.py)
	PUNCHWALK_CACHE=readwrite uv run python eval/run_eval.py --live

probe:
	$(call require,scripts/probe.py)
	PUNCHWALK_CACHE=readwrite uv run python scripts/probe.py

record:
	$(call require,scripts/record.py)
	uv run python scripts/record.py

seed:
	$(call require,scripts/seed.py)
	uv run python scripts/seed.py

build:
	$(call require,web/package.json)
	pnpm -C web build

screenshots:
	$(call require,web/package.json)
	pnpm -C web screenshots

docker:
	$(call require,Dockerfile)
	docker build -t punchwalk .

deploy:
	$(call require,fly.toml)
	fly deploy
