"""Per-country research agent + escalation predicate.

CleaningAgent class is added in Task 9 of the implementation plan.
"""
from __future__ import annotations

from cleaning.flags import FlagType
from cleaning.types import CleaningOutput


_VALID_COUNTRIES_FULL = {"Canada", "United States", "Netherlands", "Mexico", "Japan"}
_VALID_COUNTRIES_CODE = {"CA", "USA", "NL", "MX", "JP"}


def needs_escalation(output: CleaningOutput) -> list[FlagType]:
    """Decide which (if any) flags should trigger an escalation pass for this record.

    Pure function over the record + validation_notes. Returns a list of FlagType —
    empty means the record is fully resolved and does not need escalation.
    """
    rec = output.cleaned_record
    flags: list[FlagType] = []

    country = (rec.get("country") or "").strip()
    if (not country
        or country not in _VALID_COUNTRIES_FULL
           and country.upper() not in _VALID_COUNTRIES_CODE):
        flags.append(FlagType.UNKNOWN_COUNTRY)

    postal = (rec.get("postal_code") or "").strip()
    if not postal or postal.upper() == "N/A":
        flags.append(FlagType.POSTAL_UNRESOLVED)
    elif postal.endswith("?"):
        flags.append(FlagType.POSTAL_AMBIGUOUS)

    muni = (rec.get("municipality") or "").strip()
    if not muni or muni.upper() == "N/A":
        flags.append(FlagType.MUNICIPALITY_UNRESOLVED)

    notes = (rec.get("validation_notes") or "").upper()
    if "LOW" in notes and "CONFIDENCE" in notes:
        flags.append(FlagType.LOW_CONFIDENCE_RESEARCH)

    # TODO: add CROSS_REGION_MISMATCH detection (e.g. Canadian postal first letter
    # doesn't match province) when a postal-pattern library is available. The
    # FlagType value is already defined; implement as a follow-up task once a
    # lightweight CA/USA/NL/MX/JP postal-format validator is chosen.

    return flags
