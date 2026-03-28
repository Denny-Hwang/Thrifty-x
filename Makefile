# Makefile for common tasks

.PHONY: help
help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  init       to initialize pip requirements"
	@echo "  test       to run unit tests"
	@echo "  lint       to lint the source files"
	@echo "  docs       to generate Sphinx html docs"
	@echo "  dev        to install thrifty using setup.py in editable mode"
	@echo "  venv       to setup a virtualenv"

.PHONY: init
init:
	pip install -r requirements.txt

.PHONY: test
test:
	pytest tests

.PHONY: lint
lint:
	ruff check thriftyx/ tests/

.PHONY: docs
docs:
	cd docs && $(MAKE) html

.PHONY: dev
dev:
	pip install --user -e .

.PHONY: venv
venv:
	@echo "Note: remember to install SciPy's dependencies"
	@echo "(refer to http://stackoverflow.com/a/31840553)"
	@echo ""
	virtualenv venv; . venv/bin/activate; make init; pip install -e .; deactivate
	@echo "Run '. venv/bin/activate' to enter the virtual environment"
	@echo "Run 'deactivate' to leave the virtual environment"
