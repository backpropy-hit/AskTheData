"""Run the real Streamlit script with Streamlit's AppTest, using a fake LLM.

No network and no real data: the pipeline is swapped for one that uses the
small test database and a FakeLLM, and the history goes to a temporary file.
"""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import askdata.config
from askdata.config import load_config
from askdata.db.connection import connect_read_only
from askdata.history import QueryHistory
from askdata.llm.prompts import REFUSAL_REPLY, PromptStrategy
from askdata.nl2sql.pipeline import Pipeline, PipelineResult
from fakes import FakeLLM

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
TREND_SQL = "SELECT year, SUM(refugees) AS refugees FROM displacement GROUP BY year ORDER BY year"


def write_test_config(tmp_path: Path, db_path: Path, max_questions: int) -> Path:
    """Write a config file that points at the test database and a temporary history."""
    text = f"""
model:
  name: fake-llm
  temperature: 0
  rate_limit_retries: 0
  rate_limit_wait_seconds: 0
paths:
  database: {db_path.as_posix()}
  history: {(tmp_path / "history.db").as_posix()}
  raw_data: raw
  llm_cache: cache
  questions: questions.jsonl
  results: results
query:
  row_limit: 100
  timeout_seconds: 5
  sql_retries: 1
app:
  max_questions_per_session: {max_questions}
"""
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def app(tmp_path: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return a function that starts the app with the given fake LLM replies."""

    def start(replies: list, max_questions: int = 15) -> AppTest:
        """Patch the pipeline to use FakeLLM(replies), then run the app once."""
        config_path = write_test_config(tmp_path, db_path, max_questions)
        monkeypatch.setattr(askdata.config, "load_config", lambda: load_config(config_path))
        st.cache_resource.clear()  # do not reuse a pipeline from another test

        def fake_from_config(config, strategy=PromptStrategy.FEW_SHOT, max_retries=None):
            """Build a pipeline like from_config does, but with the fake LLM."""
            return Pipeline(
                client=FakeLLM(replies),
                connection=connect_read_only(config.paths.database),
                strategy=strategy,
                row_limit=config.query.row_limit,
                timeout_seconds=config.query.timeout_seconds,
                max_retries=config.query.sql_retries,
            )

        monkeypatch.setattr(Pipeline, "from_config", fake_from_config)
        app_test = AppTest.from_file(str(APP_PATH), default_timeout=30)
        app_test.run()
        return app_test

    return start


def ask(app_test: AppTest, question: str) -> AppTest:
    """Type a question, press Ask, and rerun the app."""
    app_test.text_input[0].input(question)
    app_test.button[0].click()
    app_test.run()
    return app_test


def test_page_loads_with_safety_note(app) -> None:
    """The first page shows the title and the read-only note, without errors."""
    app_test = app([])
    assert not app_test.exception
    assert app_test.title[0].value == "Ask the Data"
    assert any("read-only" in caption.value for caption in app_test.caption)


def test_question_shows_sql_table_and_chart(app) -> None:
    """A good answer shows the SQL, the table and a line chart (year + number)."""
    app_test = ask(app([TREND_SQL]), "Refugees per year?")
    assert not app_test.exception
    assert "SUM(refugees)" in app_test.code[0].value
    assert app_test.success[0].value.startswith("Query ran")
    table = app_test.dataframe[0].value
    assert list(table.columns) == ["year", "refugees"]
    assert len(table) == 2
    # st.line_chart / st.bar_chart appear as "vega_lite_chart" elements in AppTest.
    assert len(app_test.get("vega_lite_chart")) == 1


def test_no_chart_for_single_value(app) -> None:
    """A one-number answer gets a table but no chart."""
    sql = "SELECT refugees FROM displacement WHERE origin_iso3 = 'SOM' AND asylum_iso3 = 'KEN'"
    app_test = ask(app([sql]), "Somali refugees in Kenya?")
    assert len(app_test.dataframe) >= 1
    assert len(app_test.get("vega_lite_chart")) == 0


def test_refusal_shows_info_and_no_sql(app) -> None:
    """An unanswerable question shows an info message and no SQL block."""
    app_test = ask(app([REFUSAL_REPLY]), "Refugees in 2040?")
    assert not app_test.exception
    assert "cannot be answered" in app_test.info[0].value
    assert len(app_test.code) == 0


def test_blocked_sql_shows_clear_error(app) -> None:
    """Twice-blocked SQL shows the safety-check error and the blocked query."""
    app_test = ask(app(["DROP TABLE countries", "DELETE FROM countries"]), "Delete it")
    assert not app_test.exception
    assert "safety check" in app_test.error[0].value
    assert "DELETE" in app_test.code[0].value


def test_history_panel_lists_asked_questions(app) -> None:
    """After asking, the question appears in the history panel."""
    app_test = ask(app([TREND_SQL]), "Refugees per year?")
    history_table = app_test.expander[0].dataframe[0].value
    assert history_table["question"].tolist() == ["Refugees per year?"]


def test_question_limit_per_session(app) -> None:
    """After the limit is reached, no more model calls are made in that session."""
    app_test = app([TREND_SQL, TREND_SQL, TREND_SQL], max_questions=2)
    ask(app_test, "first")
    ask(app_test, "second")
    ask(app_test, "third")
    assert not app_test.exception
    assert any("limit of 2 questions" in warning.value for warning in app_test.warning)
    assert any("2 of 2" in caption.value for caption in app_test.caption)
    history_table = app_test.expander[0].dataframe[0].value
    assert history_table["question"].tolist() == ["second", "first"]


def test_history_shows_only_this_session(app, tmp_path: Path) -> None:
    """Questions logged by another session do not appear in this session's panel."""
    other = QueryHistory(tmp_path / "history.db")
    other.log(PipelineResult(question="someone else's question", status="ok"), session_id="x")

    app_test = ask(app([TREND_SQL]), "my question")
    history_table = app_test.expander[0].dataframe[0].value
    assert history_table["question"].tolist() == ["my question"]


def test_missing_database_shows_setup_error(app, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the database is missing, the app explains how to build it."""
    app_test = app([])  # first start normally to set up patches

    def missing_database(*args, **kwargs):
        """Pretend the database file does not exist."""
        raise FileNotFoundError("Database not found. Build it with python -m askdata.db.loader")

    st.cache_resource.clear()
    monkeypatch.setattr(Pipeline, "from_config", missing_database)
    app_test = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app_test.run()
    assert not app_test.exception
    assert "askdata.db.loader" in app_test.error[0].value
