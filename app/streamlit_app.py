"""Ask the Data: Streamlit interface.

Run with:  streamlit run app/streamlit_app.py

This file only handles layout. All logic (generation, validation, execution,
history, chart choice, building the database) lives in the askdata package so it
can be tested.
"""

import os
import uuid

import pandas as pd
import streamlit as st

from askdata.config import Config, load_config
from askdata.db.setup import ensure_database
from askdata.display import choose_chart, status_message
from askdata.history import QueryHistory
from askdata.llm.client import API_KEY_VARIABLE, LLMError
from askdata.nl2sql.pipeline import Pipeline, PipelineResult

st.set_page_config(page_title="Ask the Data", page_icon="📊", layout="wide")


def copy_api_key_from_secrets() -> None:
    """On Streamlit Cloud the key is stored in the app's secrets; copy it to the environment.

    Locally there is usually no secrets file and the key comes from .env instead,
    so a missing secrets file is not an error.
    """
    if os.environ.get(API_KEY_VARIABLE):
        return
    try:
        key = st.secrets.get(API_KEY_VARIABLE)
    except FileNotFoundError:
        # Streamlit's "no secrets file" error is a subclass of FileNotFoundError.
        key = None
    if key:
        os.environ[API_KEY_VARIABLE] = str(key)


@st.cache_resource(
    show_spinner="Downloading the data and building the database (first start only) ..."
)
def prepare_database(_config: Config) -> None:
    """Build askdata.db once if it is missing (the repository does not contain it).

    The leading underscore tells Streamlit not to hash the config object.
    """
    ensure_database(_config.paths.raw_data, _config.paths.database)


@st.cache_resource
def get_pipeline(_config: Config) -> Pipeline:
    """Create the pipeline once and reuse it across reruns.

    One shared connection is fine for a small app: queries are short and capped
    by the time limit.
    """
    return Pipeline.from_config(_config)


def show_result(result: PipelineResult) -> None:
    """Show the SQL, the outcome message, the result table and a chart if suitable."""
    message = status_message(result)
    if message.level == "success":
        st.success(message.text)
    elif message.level == "info":
        st.info(message.text)
    else:
        st.error(message.text)

    if result.sql:
        st.markdown("**Generated SQL**")
        st.code(result.sql, language="sql")

    if result.status != "ok":
        return

    table = pd.DataFrame(result.rows, columns=result.columns)
    st.dataframe(table, hide_index=True)

    chart = choose_chart(result.columns, result.rows)
    if chart is not None and chart.kind == "line":
        st.line_chart(table, x=chart.x, y=chart.y)
    elif chart is not None and chart.kind == "bar":
        st.bar_chart(table, x=chart.x, y=chart.y)


def show_history(history: QueryHistory, session_id: str) -> None:
    """Show this session's recent questions in a collapsible panel."""
    with st.expander("Your question history (most recent first)"):
        entries = history.recent(limit=20, session_id=session_id)
        if not entries:
            st.caption("No questions asked yet in this session.")
            return
        table = pd.DataFrame(
            [
                {
                    "asked at (UTC)": entry.asked_at,
                    "question": entry.question,
                    "status": entry.status,
                    "rows": entry.row_count,
                    "seconds": entry.latency_seconds,
                    "sql": entry.sql,
                }
                for entry in entries
            ]
        )
        st.dataframe(table, hide_index=True)


def main() -> None:
    """Build the page."""
    config = load_config()
    history = QueryHistory(config.paths.history)
    copy_api_key_from_secrets()

    # A random id per browser session, used for the per-session history and limit.
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid.uuid4().hex
        st.session_state["questions_asked"] = 0
    session_id = st.session_state["session_id"]
    limit = config.app.max_questions_per_session

    st.title("Ask the Data")
    st.write(
        "Ask a question about refugees, asylum-seekers, internally displaced people "
        "and population (UNHCR and World Bank data, 2016 to 2025)."
    )
    st.caption(
        "Safety: every generated query is checked before it runs (one SELECT only, "
        "known tables only, row limit added) and runs on a read-only database connection."
    )

    try:
        prepare_database(config)
        pipeline = get_pipeline(config)
    except (FileNotFoundError, LLMError) as error:
        # Missing database or missing API key: show how to fix it instead of a traceback.
        st.error(str(error))
        st.stop()
    except OSError as error:
        # The first-start download failed (e.g. no internet access).
        st.error(f"Could not download the data to build the database: {error}")
        st.stop()

    with st.form("question_form"):
        question = st.text_input(
            "Your question",
            placeholder="e.g. Which 5 countries hosted the most asylum-seekers in 2024?",
        )
        submitted = st.form_submit_button("Ask")

    if submitted and st.session_state["questions_asked"] >= limit:
        st.warning(f"This session has reached its limit of {limit} questions.")
    elif submitted and question.strip():
        with st.spinner("Generating and running SQL ..."):
            result = pipeline.ask(question.strip())
        st.session_state["questions_asked"] += 1
        history.log(result, model=config.model.name, session_id=session_id)
        # Keep the answer on screen when Streamlit reruns the script.
        st.session_state["last_result"] = result
    elif submitted:
        st.warning("Please type a question first.")

    if "last_result" in st.session_state:
        show_result(st.session_state["last_result"])

    show_history(history, session_id)
    asked = st.session_state["questions_asked"]
    st.caption(f"Model: {config.model.name} · Questions this session: {asked} of {limit}")


main()
