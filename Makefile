# Ask the Data: common commands. Run `make <target>` from the repo root.
# On Windows without make, run the command shown under each target directly.

PYTHON ?= python

.PHONY: install lint format test load eval app

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

load:     ## Build data/askdata.db from the CSVs in data/raw
	$(PYTHON) -m askdata.db.loader

eval:     ## Run the evaluation for all prompt strategies
	$(PYTHON) -m askdata.evaluation.run_eval

app:      ## Start the Streamlit app
	$(PYTHON) -m streamlit run app/streamlit_app.py
