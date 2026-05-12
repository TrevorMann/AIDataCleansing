"""Domain-agnostic record linker — config-driven match rules, transitive grouping."""

from typing import Any, Dict, List, Optional

from skills.base import BaseSkill


def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if not s2:
        return len(s1)
    prev = range(len(s2) + 1)
    for c1 in s1:
        curr = [prev[0] + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


def _char_bigrams(s: str) -> set:
    """Return the set of character bigrams in s."""
    return set(s[i : i + 2] for i in range(len(s) - 1))


def _fuzzy_similarity(a: str, b: str) -> float:
    """50% character-bigram Jaccard + 50% Levenshtein char similarity.

    Character bigrams correctly handle near-identical single words
    (e.g. 'smith' vs 'smyth') where whole-word token Jaccard would
    score 0 due to disjoint token sets.
    """
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    bg_a, bg_b = _char_bigrams(a), _char_bigrams(b)
    token_sim = len(bg_a & bg_b) / max(len(bg_a | bg_b), 1)
    max_len = max(len(a), len(b))
    char_sim = 1.0 - _levenshtein(a, b) / max_len
    return 0.5 * token_sim + 0.5 * char_sim


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        self.parent[self.find(x)] = self.find(y)


class RecordLinker(BaseSkill):
    """Link records that refer to the same real-world entity.

    Never modifies source field values.
    Per-record mode: run(record, tools={"candidates": [...]}) → _linked_records
    Batch mode: link_batch(records) → records with _group_id assigned
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.blocking_fields: List[str] = self.config.get("blocking_fields", [])
        self.match_rules: List[Dict] = self.config.get("match_rules", [])

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        self.clear_audit()
        candidates = (tools or {}).get("candidates", [])
        linked = []
        for candidate in candidates:
            if candidate.get("id") == input_data.get("id"):
                continue
            match = self._apply_rules(input_data, candidate)
            if match:
                linked.append(match)
                self.log_decision(
                    f"linked {input_data.get('id')} → {candidate.get('id')} via {match['matched_rule']}",
                    f"confidence={match['confidence']:.2f}",
                    confidence=match["confidence"],
                )
        if linked:
            input_data["_linked_records"] = linked
        return input_data

    def link_batch(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Full batch pass: find all matches, apply Union-Find, assign _group_id."""
        ids = [r["id"] for r in records]
        uf = _UnionFind(ids)

        blocks = self._build_blocks(records)

        for block_records in blocks.values():
            for i, rec_a in enumerate(block_records):
                for rec_b in block_records[i + 1:]:
                    match = self._apply_rules(rec_a, rec_b)
                    if match:
                        uf.union(rec_a["id"], rec_b["id"])

        for record in records:
            record["_group_id"] = uf.find(record["id"])

        return records

    def _build_blocks(self, records: List[Dict]) -> Dict[str, List[Dict]]:
        if not self.blocking_fields:
            return {"__all__": records}
        blocks: Dict[str, List[Dict]] = {}
        for record in records:
            key = tuple(str(record.get(f, "")) for f in self.blocking_fields)
            blocks.setdefault(str(key), []).append(record)
        return blocks

    def _apply_rules(self, rec_a: Dict, rec_b: Dict) -> Optional[Dict]:
        for rule in self.match_rules:
            fields = rule["fields"]
            match_type = rule["match_type"]
            weight = rule.get("weight", 1.0)
            threshold = rule.get("threshold", 1.0)

            vals_a = [str(rec_a.get(f, "")).lower().strip() for f in fields]
            vals_b = [str(rec_b.get(f, "")).lower().strip() for f in fields]

            if any(not v for v in vals_a + vals_b):
                continue  # skip rule if any required field is empty

            if match_type == "exact":
                if vals_a == vals_b:
                    return {"id": rec_b.get("id"), "matched_rule": rule["name"], "confidence": weight}

            elif match_type == "fuzzy":
                combined_a = " ".join(vals_a)
                combined_b = " ".join(vals_b)
                sim = _fuzzy_similarity(combined_a, combined_b)
                if sim >= threshold:
                    confidence = sim * weight
                    return {"id": rec_b.get("id"), "matched_rule": rule["name"], "confidence": round(confidence, 3)}

        return None
