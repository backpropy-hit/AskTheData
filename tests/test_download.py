"""Tests for askdata.db.download. No network: zips are built in memory."""

import io
import zipfile
from pathlib import Path

import pytest

from askdata.db.download import ZipMember, extract_members, sha256_of


def make_zip(files: dict[str, str]) -> bytes:
    """Build a zip file in memory from {name: text} and return its bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_extracts_by_prefix_and_renames(tmp_path: Path) -> None:
    """Versioned World Bank names are matched by prefix and saved under our names."""
    zip_bytes = make_zip(
        {
            "API_SP.POP.TOTL_DS2_en_csv_v2_264687.csv": "population",
            "Metadata_Country_API_SP.POP.TOTL_DS2_en_csv_v2_264687.csv": "countries",
            "Metadata_Indicator_API_SP.POP.TOTL_DS2_en_csv_v2_264687.csv": "unused",
        }
    )
    members = [
        ZipMember("API_SP.POP.TOTL", Path("worldbank") / "wb_population.csv"),
        ZipMember("Metadata_Country_", Path("worldbank") / "wb_country_metadata.csv"),
    ]
    written = extract_members(zip_bytes, members, tmp_path)

    assert (tmp_path / "worldbank" / "wb_population.csv").read_text() == "population"
    assert (tmp_path / "worldbank" / "wb_country_metadata.csv").read_text() == "countries"
    assert len(written) == 2
    # Files that were not asked for are not written.
    assert not list(tmp_path.rglob("Metadata_Indicator*"))


def test_missing_member_raises(tmp_path: Path) -> None:
    """If the publisher renames a file, the download fails with a clear message."""
    zip_bytes = make_zip({"something_else.csv": "x"})
    with pytest.raises(ValueError, match="population.csv"):
        extract_members(zip_bytes, [ZipMember("population.csv", Path("p.csv"))], tmp_path)


def test_ambiguous_member_raises(tmp_path: Path) -> None:
    """Two files matching one prefix is an error, not a silent guess."""
    zip_bytes = make_zip({"API_A.csv": "1", "API_B.csv": "2"})
    with pytest.raises(ValueError, match="exactly one"):
        extract_members(zip_bytes, [ZipMember("API_", Path("p.csv"))], tmp_path)


def test_path_in_archive_cannot_escape_raw_dir(tmp_path: Path) -> None:
    """A malicious name like '../evil.csv' is never used as an output path."""
    raw_dir = tmp_path / "raw"
    zip_bytes = make_zip({"../population.csv": "data"})
    # The prefix does not match "../population.csv", so nothing is written at all.
    with pytest.raises(ValueError):
        extract_members(zip_bytes, [ZipMember("population.csv", Path("p.csv"))], raw_dir)
    assert not (tmp_path / "population.csv").exists()


def test_sha256_of(tmp_path: Path) -> None:
    """Checksum matches the known SHA-256 of the text 'abc'."""
    path = tmp_path / "abc.txt"
    path.write_bytes(b"abc")
    assert sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
