"""Typed flag types and persistence helpers.

Flags surface unresolved or noteworthy issues raised during cleaning. They live
in their own table (see database.py and spec §5.3) so analytics queries like
"how many cross-region mismatches this week" are trivial.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from db_helpers import insert_flag, query_flags


class FlagType(str, Enum):
    UNKNOWN_COUNTRY            = "unknown_country"
    CROSS_REGION_MISMATCH      = "cross_region_mismatch"
    POSTAL_UNRESOLVED          = "postal_unresolved"
    POSTAL_AMBIGUOUS           = "postal_ambiguous"
    MUNICIPALITY_UNRESOLVED    = "municipality_unresolved"
    LOW_CONFIDENCE_RESEARCH    = "low_confidence_research"
    GUARDRAIL_BLOCKED          = "guardrail_blocked"
    RESOLVED_AFTER_ESCALATION  = "resolved_after_escalation"


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
