"""Tests for askdata.db.setup. The download is replaced, so no network is used."""

import shutil
from pathlib import Path

import pytest

from askdata.db import setup


def test_existing_database_is_left_alone(db_path: Path, raw_dir: Path) -> None:
    """If the database exists, nothing is downloaded or rebuilt."""
    before = db_path.stat().st_mtime_ns
    assert setup.ensure_database(raw_dir, db_path) is False
    assert db_path.stat().st_mtime_ns == before


def test_builds_from_raw_files_without_download(
    raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the CSVs present, the database is built and nothing is downloaded."""

    def fail_download(folder: Path) -> None:
        """The download must not be called in this test."""
        raise AssertionError("download_all should not be called")

    monkeypatch.setattr(setup, "download_all", fail_download)
    db_path = tmp_path / "new" / "askdata.db"
    assert setup.ensure_database(raw_dir, db_path) is True
    assert db_path.is_file()


def test_downloads_when_raw_files_missing(
    raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no CSVs, the download runs first, then the database is built."""
    empty_raw_dir = tmp_path / "empty_raw"
    calls: list[Path] = []

    def fake_download(folder: Path) -> None:
        """Copy the fixture CSVs into the folder, as a real download would."""
        calls.append(folder)
        shutil.copytree(raw_dir, folder, dirs_exist_ok=True)

    monkeypatch.setattr(setup, "download_all", fake_download)
    db_path = tmp_path / "askdata.db"
    assert setup.ensure_database(empty_raw_dir, db_path) is True
    assert calls == [empty_raw_dir]
    assert db_path.is_file()
