"""Make sure the database exists, building it from downloaded CSVs if needed.

Used by the deployed app: the repository contains neither the raw CSVs nor
askdata.db, so on first start the app downloads the data and builds the database.
"""

from __future__ import annotations

from pathlib import Path

from askdata.db.download import download_all
from askdata.db.loader import UNHCR_FILE, WB_COUNTRY_FILE, WB_POPULATION_FILE, build_database

RAW_FILES = [UNHCR_FILE, WB_POPULATION_FILE, WB_COUNTRY_FILE]


def raw_files_present(raw_dir: Path) -> bool:
    """True if every CSV the loader needs is already in raw_dir."""
    return all((raw_dir / relative_path).is_file() for relative_path in RAW_FILES)


def ensure_database(raw_dir: Path, db_path: Path) -> bool:
    """Build the database if it does not exist. Returns True if it was built now.

    Downloads the raw CSVs first if any of them are missing.
    """
    if db_path.is_file():
        return False
    if not raw_files_present(raw_dir):
        download_all(raw_dir)
    build_database(raw_dir, db_path)
    return True
