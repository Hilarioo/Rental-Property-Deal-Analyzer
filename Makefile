PYTHON := venv/bin/python
PYTEST := venv/bin/pytest

.PHONY: install install-dev test test-py test-js run clean

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m playwright install chromium

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

test: test-py test-js

test-py:
	@echo "--- pytest ---"
	$(PYTEST) tests/ -v

test-js:
	@echo "--- node --test ---"
	node --test tests/calc.test.mjs

run:
	$(PYTHON) app.py

clean:
	rm -rf venv __pycache__ .pytest_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
