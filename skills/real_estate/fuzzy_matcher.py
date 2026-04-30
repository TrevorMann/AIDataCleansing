"""Fuzzy matching skill for real estate data variants."""

from typing import Any, Dict, Optional, Tuple
from skills.base import BaseSkill


class FuzzyMatcher(BaseSkill):
    """Match address/municipality variants (25 Muir Ave vs 25 Muir Avenue)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.threshold = self.config.get("threshold", 0.90)
        self.token_weight = self.config.get("token_weight", 0.5)

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        """Fuzzy match and normalize variants.

        Args:
            input_data: Record dict
            tools: Available tools

        Returns:
            Record with fuzzy match confidence
        """
        tools = tools or {}
        decisions = []

        # Match on address if present
        if "address" in input_data and input_data.get("address"):
            address = input_data["address"]
            similarity = self._compute_similarity(address, address)
            input_data["_address_fuzzy_confidence"] = similarity

        # Match on municipality if present
        if "municipality" in input_data and input_data.get("municipality"):
            municipality = input_data["municipality"]
            similarity = self._compute_similarity(municipality, municipality)
            input_data["_municipality_fuzzy_confidence"] = similarity

        return input_data

    def match(self, text1: str, text2: str) -> Tuple[float, Dict]:
        """Match two text strings, return confidence + explanation.

        Args:
            text1: First text
            text2: Second text

        Returns:
            (confidence_score, decision_log)
        """
        if text1 == text2:
            return 1.0, self.log_decision(
                f"Exact match: '{text1}'",
                "Strings are identical",
                confidence=1.0,
            )

        similarity = self._compute_similarity(text1, text2)

        if similarity >= self.threshold:
            return similarity, self.log_decision(
                f"Fuzzy match: '{text1}' ≈ '{text2}'",
                f"Similarity score: {similarity:.3f}",
                confidence=similarity,
            )

        return similarity, self.log_decision(
            f"No match: '{text1}' vs '{text2}'",
            f"Similarity below threshold ({similarity:.3f} < {self.threshold})",
            confidence=similarity,
        )

    def _compute_similarity(self, s1: str, s2: str) -> float:
        """Compute combined similarity using token + character matching.

        Args:
            s1: First string
            s2: Second string

        Returns:
            Similarity score 0.0-1.0
        """
        if s1 == s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        # Token-based similarity (word-level matching)
        tokens1 = set(s1.lower().split())
        tokens2 = set(s2.lower().split())
        token_sim = len(tokens1 & tokens2) / max(len(tokens1 | tokens2), 1)

        # Character-based similarity (Levenshtein-like)
        char_sim = self._levenshtein_similarity(s1.lower(), s2.lower())

        # Combined score: weighted average
        combined = (self.token_weight * token_sim) + ((1 - self.token_weight) * char_sim)
        return combined

    @staticmethod
    def _levenshtein_similarity(s1: str, s2: str) -> float:
        """Normalized Levenshtein distance as similarity (0.0-1.0)."""
        if len(s1) == 0 and len(s2) == 0:
            return 1.0

        distance = _levenshtein_distance(s1, s2)
        max_len = max(len(s1), len(s2))
        return 1.0 - (distance / max_len) if max_len > 0 else 0.0


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1] + [0] * len(s2)
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row[j + 1] = min(insertions, deletions, substitutions)
        prev_row = curr_row

    return prev_row[-1]
