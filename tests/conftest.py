"""Shared test fixtures: a tiny fake dataset in the same format as the real CSVs.

The real raw files are not committed, so tests (and CI) build a small database
from these hand-made rows instead.
"""

from pathlib import Path

import pytest

from askdata.db.loader import (
    UNHCR_FILE,
    WB_COUNTRY_FILE,
    WB_POPULATION_FILE,
    build_database,
)

# Same header as the real UNHCR download, including the "IDPss" typo.
UNHCR_CSV = """Year,"Country of origin","Country of origin (ISO)","Country of asylum","Country of asylum (ISO)","Refugees under UNHCR's mandate",Asylum-seekers,"Returned refugees","IDPs of concern to UNHCR","Returned IDPss","Stateless persons","Others of concern","Other people in need of international protection","Host Community"
2024,"Syrian Arab Rep.",SYR,Türkiye,TUR,1000,50,0,0,0,0,0,-,0
2024,"Syrian Arab Rep.",SYR,"Syrian Arab Rep.",SYR,0,0,0,7000,300,0,0,-,0
2024,Stateless,XXA,Kenya,KEN,0,0,0,0,0,20,0,-,0
2025,Somalia,SOM,Kenya,KEN,400,30,10,0,0,0,0,-,5
2025,"Unknown ",UNK,Türkiye,TUR,5,1,0,0,0,0,0,-,0
"""

# World Bank files start with a byte-order mark, a few metadata lines,
# and end every line with a trailing comma.
WB_POPULATION_CSV = """﻿"Data Source","World Development Indicators",

"Last Updated Date","2026-10-08",

"Country Name","Country Code","Indicator Name","Indicator Code","2023","2024","2025",
"Kenya","KEN","Population, total","SP.POP.TOTL","54000000","55000000","56000000",
"Somalia","SOM","Population, total","SP.POP.TOTL","18000000","18500000","19000000",
"Syrian Arab Republic","SYR","Population, total","SP.POP.TOTL","23000000","24000000","25000000",
"Turkiye","TUR","Population, total","SP.POP.TOTL","85000000","85500000","86000000",
"World","WLD","Population, total","SP.POP.TOTL","8000000000","8100000000","8200000000",
"""

WB_COUNTRY_CSV = """﻿"Country Code","Region","IncomeGroup","SpecialNotes","TableName",
"KEN","Sub-Saharan Africa","Lower middle income","","Kenya",
"SOM","Sub-Saharan Africa","Low income","","Somalia",
"SYR","Middle East & North Africa","Low income","","Syrian Arab Republic",
"TUR","Europe & Central Asia","Upper middle income","","Türkiye",
"WLD","","","World aggregate","World",
"""


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    """Write the fake CSVs into a raw-data folder laid out like data/raw/."""
    folder = tmp_path / "raw"
    files = {
        UNHCR_FILE: UNHCR_CSV,
        WB_POPULATION_FILE: WB_POPULATION_CSV,
        WB_COUNTRY_FILE: WB_COUNTRY_CSV,
    }
    for relative_path, text in files.items():
        path = folder / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


@pytest.fixture
def db_path(raw_dir: Path, tmp_path: Path) -> Path:
    """Build a small askdata database from the fake CSVs and return its path."""
    path = tmp_path / "db folder with space" / "askdata.db"
    build_database(raw_dir, path)
    return path
