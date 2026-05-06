"""
Programmatic seeder registration — domain-agnostic.

Writes entries into seeders/<domain>/manifest.yaml. Idempotent by name.
Use this to register new seeders without editing YAML by hand, or as the
target for LLM-generated domain initialization.

Supported seeder types
----------------------
wikipedia_fsa   Wikipedia 'List of postal codes' API (one request per letter)
statscan_shp    Stats Can lfsa + lcsd spatial join
csv_fsa         Generic CSV drop → fsa_municipality_mapping
csv_generic     Generic CSV drop → any target table (custom column mapping)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _manifest_path(domain: str) -> Path:
    return Path(__file__).parent / domain / "manifest.yaml"


def _load(domain: str) -> dict:
    path = _manifest_path(domain)
    if not path.exists():
        raise FileNotFoundError(
            f"No manifest for domain '{domain}'. "
            f"Run: python scripts/domain.py scaffold --domain {domain}"
        )
    return yaml.safe_load(path.read_text()) or {}


def _save(domain: str, manifest: dict) -> None:
    _manifest_path(domain).write_text(
        yaml.dump(manifest, default_flow_style=False, sort_keys=False)
    )


def _upsert_entry(manifest: dict, entry: dict) -> dict:
    """Replace existing entry by name, or append. Returns updated manifest."""
    seeders = manifest.get("seeders") or []
    seeders = [s for s in seeders if s.get("name") != entry["name"]]
    seeders.append(entry)
    manifest["seeders"] = seeders
    return manifest


# ---------------------------------------------------------------------------
# Public registration functions
# ---------------------------------------------------------------------------

def add_wikipedia_fsa_seeder(
    domain: str,
    name: str,
    *,
    country: str,
    letters: list[str] | None = None,
    rate_limit_seconds: float = 1.0,
    enabled: bool = True,
) -> dict:
    """
    Register a Wikipedia FSA seeder for a given country.

    letters: FSA first-letter list to fetch. None = all known letters for
             the country (currently only CA is pre-mapped; extend
             WikipediaFSASeeder._ALL_CA_LETTERS for others).

    Example:
        add_wikipedia_fsa_seeder("real_estate", "wikipedia_fsa_ON",
            country="CA", letters=["M", "K", "L", "N", "P"])
    """
    config: dict[str, Any] = {
        "country": country,
        "rate_limit_seconds": rate_limit_seconds,
    }
    if letters:
        config["letters"] = letters

    entry = {
        "name": name,
        "class": f"seeders.{domain}.wikipedia_fsa.WikipediaFSASeeder",
        "enabled": enabled,
        "refresh_cadence": "monthly",
        "license": "CC BY-SA (Wikipedia)",
        "config": config,
    }
    manifest = _upsert_entry(_load(domain), entry)
    _save(domain, manifest)
    return entry


def add_statscan_shp_seeder(
    domain: str,
    name: str,
    *,
    fsa_shapefile: str,
    csd_shapefile: str,
    country: str = "CA",
    province_pruid: str | None = None,
    enabled: bool = True,
) -> dict:
    """
    Register a Stats Can spatial-join FSA seeder.

    fsa_shapefile: path to lfsa...shp (FSA digital boundary)
    csd_shapefile: path to lcsd...shp (Census Subdivision boundary)
    province_pruid: Stats Can PRUID string to filter, e.g. "35" for Ontario.
                    None = seed all provinces in the file.

    Example:
        add_statscan_shp_seeder("real_estate", "statscan_fsa_ON",
            fsa_shapefile="F:/case_study/.../lfsa000a21a_e.shp",
            csd_shapefile="F:/case_study/.../lcsd000a25a_e.shp",
            province_pruid="35")
    """
    config: dict[str, Any] = {
        "fsa_shapefile": fsa_shapefile,
        "csd_shapefile": csd_shapefile,
        "country": country,
    }
    if province_pruid:
        config["province"] = province_pruid

    entry = {
        "name": name,
        "class": f"seeders.{domain}.statscan_shapefile.StatsCanShapefileSeeder",
        "enabled": enabled,
        "refresh_cadence": "yearly",
        "license": "Statistics Canada Open License",
        "config": config,
    }
    manifest = _upsert_entry(_load(domain), entry)
    _save(domain, manifest)
    return entry


def add_csv_fsa_seeder(
    domain: str,
    name: str,
    *,
    country: str,
    csv_path: str,
    fsa_col: str = "FSA",
    municipality_col: str = "MUNICIPALITY",
    province_col: str | None = None,
    province_default: str | None = None,
    enabled: bool = True,
) -> dict:
    """
    Register a CSV-based FSA seeder (file-drop path).

    Standard drop location: data/seeds/<domain>/fsa_prefixes/<filename>.csv
    Requires a CsvFSASeeder class in seeders/<domain>/csv_fsa.py.

    Example:
        add_csv_fsa_seeder("real_estate", "csv_fsa_ON",
            country="CA", csv_path="data/seeds/real_estate/fsa_prefixes/CA_ON.csv",
            fsa_col="FSA", municipality_col="CSD_NAME", province_default="ON")
    """
    config: dict[str, Any] = {
        "country": country,
        "csv_path": csv_path,
        "fsa_col": fsa_col,
        "municipality_col": municipality_col,
    }
    if province_col:
        config["province_col"] = province_col
    if province_default:
        config["province_default"] = province_default

    entry = {
        "name": name,
        "class": f"seeders.{domain}.csv_fsa.CsvFSASeeder",
        "enabled": enabled,
        "refresh_cadence": "as_needed",
        "license": "internal",
        "config": config,
    }
    manifest = _upsert_entry(_load(domain), entry)
    _save(domain, manifest)
    return entry


def add_csv_generic_seeder(
    domain: str,
    name: str,
    *,
    csv_path: str,
    target_table: str,
    column_map: dict[str, str],
    conflict_columns: list[str],
    seeder_class: str | None = None,
    license_note: str = "internal",
    enabled: bool = True,
) -> dict:
    """
    Register a generic CSV → any table seeder.

    column_map: {csv_column: db_column} mapping
    conflict_columns: columns forming the ON CONFLICT key
    seeder_class: fully qualified class path; defaults to
                  seeders.<domain>.csv_generic.CsvGenericSeeder

    Example:
        add_csv_generic_seeder("hospitality", "hotel_categories",
            csv_path="data/seeds/hospitality/hotel_categories.csv",
            target_table="hotel_category_lookup",
            column_map={"CATEGORY_CODE": "code", "DESCRIPTION": "label"},
            conflict_columns=["code"])
    """
    cls = seeder_class or f"seeders.{domain}.csv_generic.CsvGenericSeeder"
    entry = {
        "name": name,
        "class": cls,
        "enabled": enabled,
        "refresh_cadence": "as_needed",
        "license": license_note,
        "config": {
            "csv_path": csv_path,
            "target_table": target_table,
            "column_map": column_map,
            "conflict_columns": conflict_columns,
        },
    }
    manifest = _upsert_entry(_load(domain), entry)
    _save(domain, manifest)
    return entry


def remove_seeder(domain: str, name: str) -> bool:
    """Remove a seeder entry by name. Returns True if found and removed."""
    manifest = _load(domain)
    seeders = manifest.get("seeders") or []
    before = len(seeders)
    manifest["seeders"] = [s for s in seeders if s.get("name") != name]
    if len(manifest["seeders"]) < before:
        _save(domain, manifest)
        return True
    return False


def list_seeders(domain: str) -> list[dict]:
    """Return all seeder entries for a domain."""
    return (_load(domain).get("seeders") or [])
