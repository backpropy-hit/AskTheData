# Ask the Data: common commands. Run `make <target>` from the repo root.
# On Windows without make, run the command shown under each target directly.

PYTHON ?= python

.PHONY: install lint format test download load eval report app

install:  ## Install the package in editable mode with dev tools
	$(PYTHON) -m pip install -e ".[dev]"

lint:     ## Check style and common bugs
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:   ## Auto-format and fix what ruff can fix
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

test:     ## Run the test suite
	$(PYTHON) -m pytest

download: ## Fetch the raw CSVs into data/raw (see data/sources.md)
	$(PYTHON) -m askdata.db.download

load:     ## Build data/askdata.db from the CSVs in data/raw
	$(PYTHON) -m askdata.db.loader

eval:     ## Run the evaluation for all prompt strategies
	$(PYTHON) -m askdata.evaluation.run_eval

report:   ## Print README results tables from the latest run
	$(PYTHON) -m askdata.evaluation.report

app:      ## Start the Streamlit app
	$(PYTHON) -m streamlit run app/streamlit_app.py
