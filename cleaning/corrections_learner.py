"""Self-learning spell corrections.

When the enrichment tiers (web search, deep escalation, planned skills) change
a text-field value that looks like a misspelling fix, propose it as a
spell_corrections row so the NEXT batch resolves it deterministically at
phase 1 — no LLM or web cost.

Rules:
- Only changes made AFTER the deterministic phase are learned (the caller
  passes a post-phase-1 snapshot), so symspell/override output is never
  re-learned.
- Learned rows use source='learned' with modest confidence and never
  overwrite existing rows (ON CONFLICT DO NOTHING) — curated seeds win.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from db.schema_config import get_framework_schema

logger = logging.getLogger(__name__)

LEARNED_SOURCE = "learned"
LEARNED_CONFIDENCE = 0.75
_MIN_LENGTH = 4
_MAX_EDIT_DISTANCE = 2


def _edit_distance(a: str, b: str, cap: int) -> int:
    """Levenshtein distance, short-circuiting above cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def propose_corrections(
    before: Dict, after: Dict, text_fields: List[str],
    max_edit_distance: int = _MAX_EDIT_DISTANCE,
) -> List[Dict]:
    """Compare snapshots and return [{wrong, right}] correction candidates.

    A candidate is a small-edit change to a text field — likely a misspelling
    fix rather than a semantic replacement (which is not safe to generalize).
    """
    proposals = []
    for field in text_fields:
        old, new = before.get(field), after.get(field)
        if not isinstance(old, str) or not isinstance(new, str):
            continue
        old_l, new_l = old.strip().lower(), new.strip().lower()
        if not old_l or not new_l or old_l == new_l:
            continue
        if len(old_l) < _MIN_LENGTH or any(ch.isdigit() for ch in old_l):
            continue
        if _edit_distance(old_l, new_l, max_edit_distance) > max_edit_distance:
            continue
        # right is stored lowercase, matching load_seed_corrections; the
        # spell checker restores capitalization when applying overrides.
        proposals.append({"wrong": old_l, "right": new_l})
    return proposals


def record_learned_corrections(conn, domain: str, proposals: List[Dict]) -> int:
    """Persist proposals; existing rows (curated or learned) are never
    overwritten. Returns number of new rows written."""
    if not conn or not proposals:
        return 0
    schema = get_framework_schema()
    written = 0
    try:
        with conn.cursor() as cur:
            for p in proposals:
                cur.execute(
                    f"""
                    INSERT INTO {schema}.spell_corrections
                        (wrong, domain, right, source, confidence)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (wrong, domain) DO NOTHING
                    """,
                    (p["wrong"], domain, p["right"], LEARNED_SOURCE, LEARNED_CONFIDENCE),
                )
                written += cur.rowcount
        conn.commit()
    except Exception as e:
        logger.warning("Could not record learned corrections: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    if written:
        logger.info("Learned %d new spell correction(s) for domain %s", written, domain)
    return written
