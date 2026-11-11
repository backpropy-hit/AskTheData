# Ask the Data: Natural Language to SQL over Humanitarian Statistics

> A small, modular NLP + SQL project. Type a question in plain English, get a generated SQL query, run it safely on a SQLite database and see the result. Accuracy is **measured**, not assumed: the repo includes an evaluation with hand-written gold queries.
>
> Interface: Streamlit. Independent project; no code, text or data from any employer or other repository is reused.

---

## 1. Problem

Nonprofit staff often hold their numbers in spreadsheets and databases but cannot write SQL. A natural-language interface helps, but generated SQL can be wrong or unsafe. This project builds a careful version: it generates SQL from a question, checks the query before running it, executes it read-only, and reports how often the answer is right.

## 2. Goal

1. Load 2 to 3 public humanitarian tables into a linked SQLite database.
2. Convert a question into a single SQL `SELECT` query with an LLM (Gemini), using the database schema and a few examples in the prompt.
3. **Validate** every generated query (SELECT only, one statement, allowed tables only, row limit) and run it on a **read-only** connection.
4. On an SQL error, send the error back to the model once and retry.
5. Show the question, SQL, result table, a simple chart where it makes sense, and any error in a Streamlit app.
6. **Evaluate** execution accuracy on 20 hand-written questions, comparing prompt strategies.

**Non-goals:** multi-database support, user accounts, handling large databases, fine-tuning a model.

## 3. Related work and how this differs

Open-source NL-to-SQL apps already exist. One example is `lohitkolluri/NLP2SQL`, a Streamlit app that supports Azure OpenAI or Gemini, SQLite file upload and PostgreSQL, charts, summary statistics, query history and exports. Its README describes features; I did not see an accuracy evaluation in it.

This project is deliberately narrower and focuses on what such apps often skip:

| | Feature-rich NL-to-SQL apps | Ask the Data |
|---|---|---|
| Scope | Many databases, charts, exports | One fixed SQLite database |
| Focus | Features and interface | **Accuracy measurement and safe execution** |
| Evidence | Demo | Evaluation table, ablations and failure analysis |
| Domain | Any data | Humanitarian statistics with joins and ambiguous wording |

Do not copy code or README text from other repositories. Learn from them, then write your own.

## 4. What makes this project meaningful

| Skill shown | Where in the project |
|---|---|
| NLP: prompt design, few-shot examples, self-correction, handling unanswerable questions | `llm/`, `nl2sql/` |
| SQL: multi-table schema, joins, aggregates, read-only access, query validation | `db/`, `safety/`, gold queries |
| Evaluation: execution accuracy, ablations, failure taxonomy | `evaluation/`, README results |
| Engineering: modular code, config, tests, CI | whole repo |

## 5. Data

Pick 2 to 3 public tables that join on a country code. Check each source's licence and cite it in the README.

| Candidate source | What it offers | Notes |
|---|---|---|
| UNHCR Refugee Data Finder (unhcr.org/refugee-statistics) | Refugees and asylum-seekers by country of origin, asylum country and year | Downloadable CSV |
| Humanitarian Data Exchange (data.humdata.org) | Many humanitarian datasets | Licence differs per dataset |
| World Bank Open Data (data.worldbank.org) | Population and income indicators by country and year | Downloadable CSV |

I have not opened these sources in this proposal; confirm the exact files, columns and licences yourself.

**Example schema** (adapt names to the real files):

```sql
CREATE TABLE countries (
  iso3          TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  region        TEXT,
  income_group  TEXT
);

CREATE TABLE population (
  iso3   TEXT REFERENCES countries(iso3),
  year   INTEGER,
  total  INTEGER,
  PRIMARY KEY (iso3, year)
);

CREATE TABLE displacement (
  year          INTEGER,
  origin_iso3   TEXT REFERENCES countries(iso3),
  asylum_iso3   TEXT REFERENCES countries(iso3),
  refugees      INTEGER,
  asylum_seekers INTEGER
);
CREATE INDEX idx_disp_year ON displacement(year);
```

The two country columns in `displacement` (origin and asylum) make some questions need a join through `countries` twice. Good for the "hard" cases.

If the data files are small and the licence allows redistribution, commit them. Otherwise commit `data/sources.md` (URLs, licence, download date) and a `load_data.py` that builds the database.

## 6. System design

```
question ─► prompt (schema + sample values + few-shot examples)
              │
              ▼
          LLM (Gemini, temperature 0) ─► candidate SQL
              │
              ▼
          validator (sqlglot: one SELECT, allowed tables, add LIMIT)
              │ reject ─► error shown / sent back to model once
              ▼
          read-only SQLite connection ─► result rows
              │ SQL error ─► retry once with the error message
              ▼
          Streamlit: SQL, table, optional chart, history
```

### Design decisions

- **Prompt contents:** `CREATE TABLE` statements, one-line descriptions for each column, and a few real example values for categorical columns (for example region names), because models guess these wrong otherwise.
- **Few-shot examples:** 4 to 5 question/SQL pairs. **They must not appear in the evaluation set**, or the accuracy number is inflated.
- **Validation:** parse with `sqlglot`; accept only a single `SELECT` statement; reject `ATTACH`, `PRAGMA` and anything else not on the allow-list; allow only known tables; add `LIMIT` if absent.
- **Read-only execution:** open SQLite with `file:askdata.db?mode=ro` (URI mode) so even a validator bug cannot write.
- **Unanswerable questions:** the prompt tells the model to return a short "cannot answer from this data" message for questions the tables cannot answer (for example forecasts).
- **Determinism:** temperature 0; record the model name and the date you ran the evaluation.

## 7. Repository structure

```
ask-the-data/
├── README.md
├── LICENSE                      # MIT
├── pyproject.toml               # or requirements.txt
├── Makefile                     # make load / eval / app / test
├── .env.example                 # GEMINI_API_KEY=...
├── .gitignore                   # .env, *.db, __pycache__
├── configs/
│   └── config.yaml              # model name, db path, row limit, retry count
├── data/
│   ├── sources.md               # URLs, licences, download dates
│   └── raw/                     # CSVs (commit only if licence allows)
├── src/askdata/
│   ├── __init__.py
│   ├── config.py
│   ├── db/
│   │   ├── loader.py            # CSV -> SQLite tables, indexes
│   │   ├── connection.py        # read-only connection helper
│   │   └── schema_text.py       # builds the schema text for the prompt
│   ├── llm/
│   │   ├── client.py            # Gemini call wrapper
│   │   └── prompts.py           # system prompt, few-shot examples
│   ├── nl2sql/
│   │   ├── generate.py          # question -> SQL
│   │   └── pipeline.py          # generate -> validate -> run -> retry
│   ├── safety/
│   │   └── validator.py         # sqlglot checks, LIMIT enforcement
│   ├── history.py               # query log in a separate SQLite file
│   └── evaluation/
│       ├── metrics.py           # execution accuracy, result comparison
│       └── run_eval.py          # runs each prompt strategy, writes table
├── app/
│   └── streamlit_app.py         # thin UI; no logic here
├── eval/
│   ├── questions.jsonl          # id, question, gold_sql, difficulty, answerable
│   └── results/                 # CSV of every run
├── tests/
│   ├── test_validator.py
│   ├── test_metrics.py
│   └── test_schema_text.py
├── docs/
│   ├── PROPOSAL.md
│   └── architecture.png
└── .github/workflows/ci.yml     # ruff + pytest
```

The Streamlit file only imports from `src/askdata`. Keeping logic out of the app makes it testable.

## 8. Evaluation plan

**Question set (20 questions, written by you):**

| Type | Count | Example |
|---|---|---|
| Easy: one table, simple filter | 6 | "How many refugees were hosted by [country] in [year]?" |
| Medium: aggregate, group, order | 7 | "Which 5 asylum countries hosted the most refugees in [year]?" |
| Hard: joins, ratios, two country columns | 5 | "Which region had the highest refugees per 1,000 population in [year]?" |
| Unanswerable | 2 | "How many refugees will there be in 2030?" |

For every answerable question, write the correct SQL yourself and check its output by hand.

**Metric: execution accuracy.** A generated query is correct if it returns the same rows as the gold query. Compare rows as sets of tuples, ignoring column names, unless the gold query has `ORDER BY`, then compare in order. Round decimals to a fixed precision. For unanswerable questions, count a correct refusal as a pass.

**Strategies compared:**

1. Zero-shot (schema only)
2. Few-shot (schema + examples)
3. Few-shot + one error-retry

**Also report:** how many generated queries the validator blocked, average latency, and a **failure table** that sorts every error into a type (wrong column, wrong join, wrong filter value, wrong aggregation, other) with one example each.

Report the numbers whatever they are. If retry does not help, say so.

## 9. Timeline

About 5 to 6 hours for the core version.

| Step | Work | Done when |
|---|---|---|
| 1 (1 h) | Pick data, write `sources.md`, `loader.py` | Database builds from CSVs |
| 2 (1.5 h) | `schema_text.py`, `prompts.py`, `client.py`, `generate.py`, `validator.py`, `pipeline.py` | A question returns SQL and rows in a terminal |
| 3 (1 h) | **You** write the 20 questions and gold SQL | `questions.jsonl` complete |
| 4 (1 h) | `metrics.py`, `run_eval.py`, tests | Results table for three strategies |
| 5 (1 to 1.5 h) | `streamlit_app.py`, README, architecture diagram | App runs locally; README has only measured numbers |

**Optional later:** deploy on Streamlit Community Cloud, add a second dataset, add CI.

## 10. Risks and limitations to state in the README

- Twenty questions written by one person is a small evaluation set.
- LLM outputs vary between model versions; record the model and run date.
- Only one database; results may not transfer to other schemas.
- The validator reduces risk but is not a guarantee; the read-only connection is the second layer.
- Execution accuracy can mark a wrong query as correct if both happen to return the same rows by chance.

## 11. README outline

1. One-line description, a screenshot, and a demo link if deployed.
2. The problem in three sentences.
3. Architecture diagram.
4. **Results table** (accuracy by strategy and difficulty) and the failure table.
5. Quickstart:
   ```
   pip install -e ".[dev]"
   cp .env.example .env     # add your Gemini key
   python -m askdata.db.loader
   streamlit run app/streamlit_app.py
   ```
6. Design decisions, including how you avoided evaluation leakage.
7. Limitations (section 10).

## 12. Resume text

Fill in the numbers after you run the evaluation. Do not publish placeholders.

> **Ask the Data** | Python, SQLite, sqlglot, Gemini API, Streamlit
> - Built a natural-language-to-SQL tool over [N] linked humanitarian tables with SELECT-only validation and read-only execution.
> - Evaluated on [N] hand-written questions (easy, medium, hard, unanswerable): execution accuracy [X]% few-shot vs [Y]% zero-shot; analysed [N] failures by type.
