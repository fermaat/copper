.PHONY: install install-dev lint format mypy test check clean

install:
	pdm install

install-dev:
	pdm install -d

lint:
	pdm run ruff check src

format:
	pdm run black src

mypy:
	pdm run mypy src

test:
	pdm run pytest -q

check:
	bash run_local_checks.sh

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
