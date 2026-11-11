# Data sources

All files were downloaded on **2026-11-11** into `data/raw/`. The raw files are **not committed** (see `.gitignore`) until the licence check below is complete for every source.

Recreate them with `python -m askdata.db.download` (or `make download`), then build the database with `python -m askdata.db.loader`. The script fetches only the three files the loader uses. The footnotes and indicator-metadata files listed below came in the same downloads and are kept for reference only.

## 1. UNHCR: forcibly displaced and stateless populations

| | |
|---|---|
| Publisher | UNHCR, the UN Refugee Agency: Refugee Data Finder |
| Landing page | https://www.unhcr.org/refugee-statistics/ |
| Downloaded via | UNHCR Population Statistics API |
| Download URL | `https://api.unhcr.org/population/v1/population/?yearFrom=2016&yearTo=2025&coo_all=true&coa_all=true&download=true` |
| Files | `unhcr/unhcr_population_2016_2025.csv` (56,318 rows, one per year × origin × asylum), `unhcr/unhcr_footnotes.csv` |
| Coverage | 2016–2025, year-end figures |
| Licence | **No explicit licence found** (checked manually on 2026-11-11, see below) |
| Attribution | "UNHCR Refugee Data Finder, <https://www.unhcr.org/refugee-statistics/>" |

**Licence check status.** On 2026-11-11 the project owner checked the Refugee Data Finder website by hand and found no licence statement. Automated requests to the site returned HTTP 403, and the API documentation states no data licence either. UNHCR datasets on the Humanitarian Data Exchange list different licences per dataset (CC BY, CC BY-IGO 3.0 and CC0 were all seen), but none of those entries is this exact API download.

A missing licence does not grant redistribution rights. For that reason the raw UNHCR CSVs are not committed to this repository. Recreate them with the download URL above.

## 2. World Bank: Population, total (SP.POP.TOTL)

| | |
|---|---|
| Publisher | World Bank, World Development Indicators (last updated 2026-10-08 according to the file header) |
| Indicator page | https://data.worldbank.org/indicator/SP.POP.TOTL |
| Download URL | `https://api.worldbank.org/v2/en/indicator/SP.POP.TOTL?downloadformat=csv` (zip) |
| Files | `worldbank/wb_population.csv` (wide format, 1960–2025), `worldbank/wb_country_metadata.csv` (region and income group per country), `worldbank/wb_indicator_metadata.csv` |
| Licence | **Creative Commons Attribution 4.0 (CC BY 4.0)**, as stated in the World Bank Data Catalog entry for World Development Indicators: https://datacatalog.worldbank.org/search/dataset/0037712 |
| Attribution | "World Bank, World Development Indicators: Population, total (SP.POP.TOTL)." |

The region and income-group columns come from the metadata file in the same zip, so they fall under the same catalogue entry.

## File checksums (SHA-256)

```
e3722f0facfe7403b59d751488c6dcf9d244907f031e628eb5338cb43ee60bad  unhcr/unhcr_population_2016_2025.csv
d871fcb540f808584820928f0cb99405a62d26262e04b9ea956a551550e17ee8  unhcr/unhcr_footnotes.csv
6a283ed64894e01f7c79cf2d325fecd3c24a544f6839dcda7e2209913092422e  worldbank/wb_population.csv
eb2b248a5a2dd23d0238b210475ca8a3753d0ae7b1207828c7db377c592880a1  worldbank/wb_country_metadata.csv
f68a7b682fb54623c41e4f73185028285d3e53698f4eb7f595b8214d9cd13147  worldbank/wb_indicator_metadata.csv
```

The data publishers update their files. A later download may differ, which would change the gold query results.
