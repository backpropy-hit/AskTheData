"""Build the SQLite database (askdata.db) from the raw CSVs in data/raw/.

Run with:  python -m askdata.db.loader

Tables created:
    countries     one row per country (World Bank countries + UNHCR-only codes)
    population    total population per country and year (World Bank)
    displacement  displaced people per year, origin country and asylum country (UNHCR)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd

from askdata.config import load_config

# Raw file locations, relative to the raw data folder (see data/sources.md).
UNHCR_FILE = Path("unhcr") / "unhcr_population_2016_2025.csv"
WB_POPULATION_FILE = Path("worldbank") / "wb_population.csv"
WB_COUNTRY_FILE = Path("worldbank") / "wb_country_metadata.csv"

# Original UNHCR headers -> clean snake_case column names used in the database.
# "Other people in need of international protection" is left out on purpose:
# it is "-" (no data) in almost every row.
UNHCR_COLUMNS: dict[str, str] = {
    "Year": "year",
    "Country of origin": "origin_name",
    "Country of origin (ISO)": "origin_iso3",
    "Country of asylum": "asylum_name",
    "Country of asylum (ISO)": "asylum_iso3",
    "Refugees under UNHCR's mandate": "refugees",
    "Asylum-seekers": "asylum_seekers",
    "Returned refugees": "returned_refugees",
    "IDPs of concern to UNHCR": "idps",
    "Returned IDPss": "returned_idps",  # the double "s" is in the source file
    "Stateless persons": "stateless",
    "Others of concern": "others_of_concern",
    "Host Community": "host_community",
}

UNHCR_COUNT_COLUMNS: list[str] = [
    "refugees",
    "asylum_seekers",
    "returned_refugees",
    "idps",
    "returned_idps",
    "stateless",
    "others_of_concern",
    "host_community",
]

WB_COUNTRY_COLUMNS: dict[str, str] = {
    "Country Code": "iso3",
    "TableName": "name",
    "Region": "region",
    "IncomeGroup": "income_group",
}

SCHEMA_SQL = """
CREATE TABLE countries (
    iso3          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    region        TEXT,
    income_group  TEXT
);

CREATE TABLE population (
    iso3   TEXT NOT NULL REFERENCES countries(iso3),
    year   INTEGER NOT NULL,
    total  INTEGER,
    PRIMARY KEY (iso3, year)
);

CREATE TABLE displacement (
    year               INTEGER NOT NULL,
    origin_iso3        TEXT NOT NULL REFERENCES countries(iso3),
    asylum_iso3        TEXT NOT NULL REFERENCES countries(iso3),
    refugees           INTEGER NOT NULL,
    asylum_seekers     INTEGER NOT NULL,
    returned_refugees  INTEGER NOT NULL,
    idps               INTEGER NOT NULL,
    returned_idps      INTEGER NOT NULL,
    stateless          INTEGER NOT NULL,
    others_of_concern  INTEGER NOT NULL,
    host_community     INTEGER NOT NULL,
    PRIMARY KEY (year, origin_iso3, asylum_iso3)
);

CREATE INDEX idx_disp_year ON displacement(year);
CREATE INDEX idx_disp_origin ON displacement(origin_iso3);
CREATE INDEX idx_disp_asylum ON displacement(asylum_iso3);
CREATE INDEX idx_countries_region ON countries(region);
"""


def read_unhcr(path: Path) -> pd.DataFrame:
    """Read the UNHCR population CSV and return it with clean column names.

    Count columns are converted to integers. If a count cell is not a whole
    number, pandas raises an error, so a format change in the source is noticed.
    """
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)

    missing = [column for column in UNHCR_COLUMNS if column not in raw.columns]
    if missing:
        raise ValueError(f"{path.name} is missing expected columns: {missing}")

    data = raw[list(UNHCR_COLUMNS)].rename(columns=UNHCR_COLUMNS)

    # Some names carry stray spaces in the source, e.g. "Unknown ".
    for column in ["origin_name", "origin_iso3", "asylum_name", "asylum_iso3"]:
        data[column] = data[column].str.strip()

    data["year"] = pd.to_numeric(data["year"], errors="raise").astype("int64")
    for column in UNHCR_COUNT_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="raise").astype("int64")
    return data


def read_wb_countries(path: Path) -> pd.DataFrame:
    """Read World Bank country metadata, keeping real countries only.

    Aggregates such as "World" or "Sub-Saharan Africa" have an empty Region.
    They are dropped so that SUM queries do not double-count.
    """
    # utf-8-sig removes the byte-order mark at the start of World Bank files.
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    data = raw[list(WB_COUNTRY_COLUMNS)].rename(columns=WB_COUNTRY_COLUMNS)
    data = data[data["region"].notna()].copy()

    for column in ["iso3", "name", "region", "income_group"]:
        data[column] = data[column].str.strip()
    return data.reset_index(drop=True)


def _find_header_row(path: Path, first_column: str) -> int:
    """Return the 0-based line number of the header row that starts with first_column.

    World Bank indicator files begin with a few lines of metadata
    ("Data Source", "Last Updated Date") before the real header.
    """
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file):
            if line.startswith(f'"{first_column}"'):
                return line_number
    raise ValueError(f"Could not find a header starting with {first_column!r} in {path.name}")


def read_wb_population(path: Path, years: list[int]) -> pd.DataFrame:
    """Read the wide World Bank population file and return long format.

    The file has one column per year (1960, 1961, ...). The result has one row
    per (iso3, year) with columns iso3, year, total, for the requested years only.
    """
    header_row = _find_header_row(path, "Country Name")
    raw = pd.read_csv(path, skiprows=header_row, dtype=str, encoding="utf-8-sig")

    year_columns = [str(year) for year in years]
    missing = [column for column in year_columns if column not in raw.columns]
    if missing:
        raise ValueError(f"{path.name} has no data for years: {missing}")

    wide = raw[["Country Code", *year_columns]].rename(columns={"Country Code": "iso3"})
    long = wide.melt(id_vars="iso3", var_name="year", value_name="total")
    long["year"] = long["year"].astype("int64")
    # Nullable integer type, so a missing population stays NULL instead of NaN.
    long["total"] = pd.to_numeric(long["total"], errors="raise").astype("Int64")
    return long.sort_values(["iso3", "year"]).reset_index(drop=True)


def build_countries_table(wb_countries: pd.DataFrame, unhcr: pd.DataFrame) -> pd.DataFrame:
    """Combine World Bank countries with codes that appear only in UNHCR data.

    UNHCR uses some codes the World Bank does not, such as XXA (Stateless) or
    UNK (Unknown). They get a row with the UNHCR name and NULL region and
    income group, so every displacement row has a valid foreign key.
    """
    origin = unhcr[["origin_iso3", "origin_name"]].set_axis(["iso3", "name"], axis=1)
    asylum = unhcr[["asylum_iso3", "asylum_name"]].set_axis(["iso3", "name"], axis=1)
    unhcr_codes = pd.concat([origin, asylum]).drop_duplicates(subset="iso3")

    extra = unhcr_codes[~unhcr_codes["iso3"].isin(wb_countries["iso3"])].copy()
    extra["region"] = None
    extra["income_group"] = None

    countries = pd.concat([wb_countries, extra], ignore_index=True)
    return countries.sort_values("iso3").reset_index(drop=True)


def build_database(raw_dir: Path, db_path: Path) -> dict[str, int]:
    """Build the SQLite database from the raw CSVs and return row counts per table.

    The database is written to a temporary file first and then moved into place,
    so a failed build never leaves a half-written askdata.db behind.
    """
    unhcr = read_unhcr(raw_dir / UNHCR_FILE)
    wb_countries = read_wb_countries(raw_dir / WB_COUNTRY_FILE)

    # Keep population only for the years that the displacement data covers.
    years = sorted(unhcr["year"].unique().tolist())
    population = read_wb_population(raw_dir / WB_POPULATION_FILE, years)

    countries = build_countries_table(wb_countries, unhcr)
    # The population file also contains aggregates; keep known countries only.
    population = population[population["iso3"].isin(countries["iso3"])]
    displacement = unhcr[["year", "origin_iso3", "asylum_iso3", *UNHCR_COUNT_COLUMNS]]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_suffix(".db.tmp")
    if temp_path.exists():
        temp_path.unlink()

    connection = sqlite3.connect(temp_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_SQL)
        # Insert parents (countries) before children so foreign keys are satisfied.
        countries.to_sql("countries", connection, if_exists="append", index=False)
        population.to_sql("population", connection, if_exists="append", index=False)
        displacement.to_sql("displacement", connection, if_exists="append", index=False)
        connection.commit()

        problems = connection.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise ValueError(f"Foreign key check failed for {len(problems)} rows: {problems[:5]}")

        counts = {}
        for table in ["countries", "population", "displacement"]:
            counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()

    # os.replace overwrites an existing database in one step.
    os.replace(temp_path, db_path)
    return counts


def main() -> None:
    """Build the database at the path set in configs/config.yaml and print row counts."""
    config = load_config()
    print(f"Reading CSVs from {config.paths.raw_data}")
    counts = build_database(config.paths.raw_data, config.paths.database)
    print(f"Built {config.paths.database}")
    for table, count in counts.items():
        print(f"  {table:<13} {count:>7,} rows")


if __name__ == "__main__":
    main()
