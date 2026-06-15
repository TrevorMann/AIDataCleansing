"""Shared gap classifier. v1 implements the `missing` verb only.

Pure function: pass the record and a pre-loaded gap_detection config so this is
DB-free and trivially testable. Load the config with
db.schema_discovery.get_gap_detection().
"""
from typing import Optional

from cleaning.gap_types import build_gap


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _qualifier_for(record: dict, cfg: dict) -> Optional[str]:
    disc = cfg.get("discriminator")
    if not disc:
        return None
    disc_val = record.get(disc)
    if _is_empty(disc_val):
        return None
    return str(disc_val)


def classify_gaps(record: dict, gap_config: dict) -> list:
    """Return de-duplicated gap-type strings for a record.

    v1: only the `missing` branch is built. malformed/out_of_range/mismatch keys
    in the config are intentionally ignored (designed, not built — see spec §7).
    """
    gaps = []
    for column, cfg in gap_config.items():
        if not cfg.get("missing"):
            continue
        if _is_empty(record.get(column)):
            gaps.append(build_gap("missing", column, qualifier=_qualifier_for(record, cfg)))
    # dedupe, preserve order
    return list(dict.fromkeys(gaps))
