.PHONY: generate check-generated lint format typecheck test build check

UV ?= uv

generate:
	$(UV) run python scripts/generate.py

check-generated:
	$(UV) run python scripts/generate.py --check

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest

build:
	rm -rf dist
	$(UV) build
	$(UV) run twine check --strict dist/*

check: check-generated lint typecheck test
