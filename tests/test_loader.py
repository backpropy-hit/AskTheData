"""Tests for askdata.db.loader, using the fake dataset from conftest.py."""

import sqlite3
from pathlib import Path

import pytest

from askdata.db.loader import UNHCR_FILE, build_database


def query(db_path: Path, sql: str) -> list[tuple]:
    """Run a query on a test database and return all rows."""
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_build_returns_row_counts(raw_dir: Path, tmp_path: Path) -> None:
    """Counts: 4 WB countries + 2 UNHCR-only codes; 4 countries x 2 years; 5 UNHCR rows."""
    counts = build_database(raw_dir, tmp_path / "test.db")
    assert counts == {"countries": 6, "population": 8, "displacement": 5}


def test_tables_and_indexes_exist(db_path: Path) -> None:
    """All three tables and the four indexes are created."""
    rows = query(db_path, "SELECT type, name FROM sqlite_master")
    tables = {name for kind, name in rows if kind == "table"}
    indexes = {name for kind, name in rows if kind == "index"}

    assert {"countries", "population", "displacement"} <= tables
    expected_indexes = {
        "idx_disp_year",
        "idx_disp_origin",
        "idx_disp_asylum",
        "idx_countries_region",
    }
    assert expected_indexes <= indexes


def test_world_bank_aggregates_are_excluded(db_path: Path) -> None:
    """'World' has no region, so it must not appear as a country or in population."""
    assert query(db_path, "SELECT * FROM countries WHERE iso3 = 'WLD'") == []
    assert query(db_path, "SELECT * FROM population WHERE iso3 = 'WLD'") == []


def test_unhcr_only_codes_are_added_with_null_region(db_path: Path) -> None:
    """XXA and UNK come only from UNHCR: kept, with NULL region and income group."""
    rows = query(
        db_path,
        "SELECT iso3, name, region, income_group FROM countries "
        "WHERE iso3 IN ('XXA', 'UNK') ORDER BY iso3",
    )
    assert rows == [("UNK", "Unknown", None, None), ("XXA", "Stateless", None, None)]


def test_world_bank_name_is_used_for_known_countries(db_path: Path) -> None:
    """For countries in both sources, the World Bank name (and UTF-8 text) is kept."""
    assert query(db_path, "SELECT name FROM countries WHERE iso3 = 'TUR'") == [("Türkiye",)]


def test_population_is_long_and_limited_to_displacement_years(db_path: Path) -> None:
    """Population has one row per (country, year), only for years in the UNHCR file."""
    years = query(db_path, "SELECT DISTINCT year FROM population ORDER BY year")
    assert years == [(2024,), (2025,)]
    total = query(db_path, "SELECT total FROM population WHERE iso3 = 'KEN' AND year = 2025")
    assert total == [(56000000,)]


def test_displacement_values_and_types(db_path: Path) -> None:
    """Counts are stored as integers in the clean column names."""
    row = query(
        db_path,
        "SELECT refugees, asylum_seekers, returned_refugees, host_community "
        "FROM displacement WHERE origin_iso3 = 'SOM' AND asylum_iso3 = 'KEN'",
    )
    assert row == [(400, 30, 10, 5)]
    types = query(db_path, "SELECT DISTINCT typeof(refugees) FROM displacement")
    assert types == [("integer",)]


def test_empty_column_is_dropped(db_path: Path) -> None:
    """'Other people in need of international protection' is not loaded."""
    columns = [row[1] for row in query(db_path, "PRAGMA table_info(displacement)")]
    assert not any("other_people" in column or "oip" in column for column in columns)
    assert len(columns) == 11


def test_foreign_keys_hold(db_path: Path) -> None:
    """Every country code in population and displacement exists in countries."""
    assert query(db_path, "PRAGMA foreign_key_check") == []


def test_rebuild_replaces_existing_database(raw_dir: Path, tmp_path: Path) -> None:
    """Running the loader twice gives the same counts, not duplicated rows."""
    path = tmp_path / "test.db"
    first = build_database(raw_dir, path)
    second = build_database(raw_dir, path)
    assert first == second
    assert not path.with_suffix(".db.tmp").exists()


def test_non_numeric_count_raises(raw_dir: Path, tmp_path: Path) -> None:
    """If the source format changes (text in a count column), the build fails loudly."""
    unhcr_path = raw_dir / UNHCR_FILE
    text = unhcr_path.read_text(encoding="utf-8").replace(",1000,", ",lots,")
    unhcr_path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        build_database(raw_dir, tmp_path / "test.db")


def test_missing_column_raises(raw_dir: Path, tmp_path: Path) -> None:
    """A renamed source column is reported by name."""
    unhcr_path = raw_dir / UNHCR_FILE
    text = unhcr_path.read_text(encoding="utf-8").replace("Asylum-seekers", "Asylum seekers")
    unhcr_path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="Asylum-seekers"):
        build_database(raw_dir, tmp_path / "test.db")
