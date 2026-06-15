"""Gap-type string vocabulary: <verb>:<field>[|<qualifier>].

The verb set is CLOSED — extending it is a deliberate code change, not config.
See docs/superpowers/specs/2026-06-15-gap-type-vocabulary-design.md.
"""
from dataclasses import dataclass
from typing import Optional, Sequence, Union

VERBS = ("missing", "malformed", "ambiguous", "mismatch", "out_of_range")


@dataclass(frozen=True)
class ParsedGap:
    verb: str
    fields: tuple           # one field, or several for `mismatch`
    qualifier: Optional[str]


def build_gap(verb: str, field: Union[str, Sequence[str]], qualifier: Optional[str] = None) -> str:
    if verb not in VERBS:
        raise ValueError(f"unknown gap verb: {verb!r} (allowed: {VERBS})")
    if isinstance(field, str):
        field_part = field
    else:
        field_part = "+".join(sorted(field))
    gap = f"{verb}:{field_part}"
    if qualifier is not None and str(qualifier).strip():
        gap += f"|{str(qualifier).strip().lower()}"
    return gap


def parse_gap(gap: str) -> ParsedGap:
    """Split a gap string into its parts. LENIENT by design — does NOT validate
    the verb. Callers needing guarantees must also call is_valid_gap().
    """
    qualifier = None
    body = gap
    if "|" in body:
        body, qualifier = body.split("|", 1)
        qualifier = qualifier.strip().lower() or None
    verb, _, field_part = body.partition(":")
    fields = tuple(field_part.split("+")) if field_part else ()
    return ParsedGap(verb, fields, qualifier)


def is_valid_gap(gap: str) -> bool:
    parsed = parse_gap(gap)
    return parsed.verb in VERBS and len(parsed.fields) >= 1 and all(parsed.fields)
