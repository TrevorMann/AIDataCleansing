"""Typed flag types and persistence helpers.

Flags surface unresolved or noteworthy issues raised during cleaning. They live
in their own table (see database.py and spec §5.3) so analytics queries like
"how many cross-region mismatches this week" are trivial.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from cleaning.gap_types import parse_gap
from db.helpers import insert_flag, query_flags


class FlagType(str, Enum):
    UNKNOWN_COUNTRY            = "unknown_country"
    CROSS_REGION_MISMATCH      = "cross_region_mismatch"
    POSTAL_UNRESOLVED          = "postal_unresolved"
    POSTAL_AMBIGUOUS           = "postal_ambiguous"
    MUNICIPALITY_UNRESOLVED    = "municipality_unresolved"
    LOW_CONFIDENCE_RESEARCH    = "low_confidence_research"
    GUARDRAIL_BLOCKED          = "guardrail_blocked"
    RESOLVED_AFTER_ESCALATION  = "resolved_after_escalation"


# Data-defect gaps -> output FlagType. Keyed on (verb, first_field).
# Process flags (guardrail_blocked, etc.) are never derived from gaps.
_GAP_TO_FLAG = {
    ("missing", "country"):      FlagType.UNKNOWN_COUNTRY,
    ("missing", "postal_code"):  FlagType.POSTAL_UNRESOLVED,
    ("missing", "municipality"): FlagType.MUNICIPALITY_UNRESOLVED,
    ("ambiguous", "postal_code"): FlagType.POSTAL_AMBIGUOUS,
}


def flags_from_gaps(gap_types: list) -> list:
    """Derive output FlagTypes from gap-type strings (spec §6).

    Unmapped gaps yield nothing. Result is de-duplicated, order-preserving.
    """
    flags = []
    for gap in gap_types:
        parsed = parse_gap(gap)
        first_field = parsed.fields[0] if parsed.fields else None
        flag = _GAP_TO_FLAG.get((parsed.verb, first_field))
        if flag is not None and flag not in flags:
            flags.append(flag)
    return flags


class FlagSeverity(str, Enum):
    INFO         = "INFO"
    WARN         = "WARN"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED      = "BLOCKED"


@dataclass
class Flag:
    flag_type: FlagType
    severity: FlagSeverity
    reason: str
    raised_by: str
    cleaned_data_id: Optional[int] = None


def persist_flags(
    db_path: str,
    *,
    raw_data_id: int,
    cleaned_data_id: Optional[int],
    flags: list[Flag],
) -> list[int]:
    """Persist a list of flags. Returns their new IDs in order."""
    ids = []
    for f in flags:
        cdi = f.cleaned_data_id if f.cleaned_data_id is not None else cleaned_data_id
        ids.append(insert_flag(
            db_path,
            raw_data_id=raw_data_id,
            cleaned_data_id=cdi,
            flag_type=f.flag_type.value,
            severity=f.severity.value,
            reason=f.reason,
            raised_by=f.raised_by,
        ))
    return ids


def query_unresolved_flags(db_path: str, limit: int = 100) -> list[dict]:
    """Convenience wrapper around db_helpers.query_flags(only_unresolved=True)."""
    return query_flags(db_path, only_unresolved=True, limit=limit)
