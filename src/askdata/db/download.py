"""Download the raw CSVs into data/raw/ (they are not committed to git).

Run with:  python -m askdata.db.download

The sources, licences and expected checksums are documented in data/sources.md.
The publishers update their data, so a checksum difference is reported as a
warning, not an error. It means the gold query results may change.
"""

from __future__ import annotations

import hashlib
import io
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from askdata.config import load_config
from askdata.db.loader import UNHCR_FILE, WB_COUNTRY_FILE, WB_POPULATION_FILE

DOWNLOAD_TIMEOUT_SECONDS = 120

UNHCR_URL = (
    "https://api.unhcr.org/population/v1/population/"
    "?yearFrom=2016&yearTo=2025&coo_all=true&coa_all=true&download=true"
)
WB_POPULATION_URL = "https://api.worldbank.org/v2/en/indicator/SP.POP.TOTL?downloadformat=csv"

# SHA-256 of the files downloaded on 2026-11-11 (same values as data/sources.md).
EXPECTED_SHA256: dict[Path, str] = {
    UNHCR_FILE: "e3722f0facfe7403b59d751488c6dcf9d244907f031e628eb5338cb43ee60bad",
    WB_POPULATION_FILE: "6a283ed64894e01f7c79cf2d325fecd3c24a544f6839dcda7e2209913092422e",
    WB_COUNTRY_FILE: "eb2b248a5a2dd23d0238b210475ca8a3753d0ae7b1207828c7db377c592880a1",
}


@dataclass(frozen=True)
class ZipMember:
    """One file to take out of a downloaded zip.

    The World Bank adds a version number to its file names
    (e.g. "API_SP.POP.TOTL_DS2_en_csv_v2_264687.csv"), so files are matched
    by the start of their name instead of the full name.
    """

    name_prefix: str
    target: Path


UNHCR_MEMBERS = [
    ZipMember("population.csv", UNHCR_FILE),
]
WB_MEMBERS = [
    ZipMember("API_SP.POP.TOTL", WB_POPULATION_FILE),
    ZipMember("Metadata_Country_", WB_COUNTRY_FILE),
]


def fetch_bytes(url: str) -> bytes:
    """Download a URL and return the response body."""
    request = urllib.request.Request(url, headers={"User-Agent": "askdata-downloader"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read()


def extract_members(zip_bytes: bytes, members: list[ZipMember], raw_dir: Path) -> list[Path]:
    """Write the wanted files from a zip into raw_dir and return their paths.

    Only the listed files are extracted, under names we choose. Nothing is
    extracted under the names stored in the archive, so a malicious archive
    cannot write outside raw_dir.

    Raises:
        ValueError: If a wanted file is not in the zip, or matches more than once.
    """
    written = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        for member in members:
            matches = [name for name in names if name.startswith(member.name_prefix)]
            if len(matches) != 1:
                raise ValueError(
                    f"Expected exactly one file starting with {member.name_prefix!r} "
                    f"in the download, found {matches}"
                )
            target_path = raw_dir / member.target
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(archive.read(matches[0]))
            written.append(target_path)
    return written


def sha256_of(path: Path) -> str:
    """Return the SHA-256 checksum of a file as a hex string."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_checksums(raw_dir: Path) -> list[Path]:
    """Return the files whose checksum differs from the one recorded in sources.md."""
    changed = []
    for relative_path, expected in EXPECTED_SHA256.items():
        if sha256_of(raw_dir / relative_path) != expected:
            changed.append(relative_path)
    return changed


def download_all(raw_dir: Path) -> None:
    """Download both sources into raw_dir and report any checksum differences."""
    print("Downloading UNHCR data ...")
    extract_members(fetch_bytes(UNHCR_URL), UNHCR_MEMBERS, raw_dir)
    print("Downloading World Bank population data ...")
    extract_members(fetch_bytes(WB_POPULATION_URL), WB_MEMBERS, raw_dir)

    changed = check_checksums(raw_dir)
    if changed:
        print("WARNING: these files differ from the versions recorded in data/sources.md:")
        for path in changed:
            print(f"  {path}")
        print("The publisher has probably updated the data; evaluation results may differ.")
    else:
        print("All files match the checksums in data/sources.md.")


def main() -> None:
    """Download the raw files into the folder set in configs/config.yaml."""
    raw_dir = load_config().paths.raw_data
    download_all(raw_dir)
    print(f"Files saved under {raw_dir}. Next: python -m askdata.db.loader")


if __name__ == "__main__":
    main()
