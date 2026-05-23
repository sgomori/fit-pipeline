.PHONY: test lint format typecheck review install clean

PYTHON := python3
PYTEST := $(PYTHON) -m pytest
BLACK  := $(PYTHON) -m black
RUFF   := $(PYTHON) -m ruff
MYPY   := $(PYTHON) -m mypy

install:
	$(PYTHON) -m pip install --user --break-system-packages -e ".[dev]"

test:
	$(PYTEST) -v

test-fast:
	$(PYTEST) -v -x

lint:
	$(RUFF) check .

format:
	$(BLACK) .
	$(RUFF) check --fix .

typecheck:
	$(MYPY) fit_pipeline/

check: lint typecheck test

review:
	@echo ""
	@echo "To run a code review, type /code-review in the Claude Code prompt."
	@echo "For highest-quality review, switch to Opus first with /fast."
	@echo ""

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
