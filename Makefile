# Makefile for common tasks

.PHONY: help
help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  dev        to install Thrifty-X (editable, all extras)"
	@echo "  test       to run the test suite"
	@echo "  lint       to run ruff + mypy (same gates as CI)"

.PHONY: dev
dev:
	pip install -e ".[all]"

.PHONY: test
test:
	pytest -q

.PHONY: lint
lint:
	ruff check thriftyx/ tests/
	mypy thriftyx/
