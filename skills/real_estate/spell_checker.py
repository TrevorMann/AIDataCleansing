"""Spell checker skill for real estate addresses and data."""

from typing import Any, Dict, Optional
from skills.base import BaseSkill


class SpellChecker(BaseSkill):
    """Fix spelling mistakes in real estate data (addresses, municipalities, etc.)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.threshold = self.config.get("threshold", 0.85)

        # Real estate domain dictionary - common misspellings
        self.corrections = {
            "scarbbrough": "scarborough",
            "scarbrough": "scarborough",
            "toronot": "toronto",
            "north yokr": "north york",
            "etobicoe": "etobicoke",
            "yorl": "york",
            "oakvile": "oakville",
            "vaughn": "vaughan",
            "postal cod": "postal code",
            "provice": "province",
            "municpality": "municipality",
        }

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        """Fix spelling mistakes in address and municipality fields.

        Args:
            input_data: Record dict
            tools: Available tools

        Returns:
            Record with spelling corrected
        """
        tools = tools or {}
        decisions = []

        # Check municipality field
        if "municipality" in input_data and input_data["municipality"]:
            corrected, decision = self._correct_text(
                input_data["municipality"], "municipality", tools
            )
            if corrected != input_data["municipality"]:
                decisions.append(decision)
                input_data["municipality"] = corrected

        # Check address field
        if "address" in input_data and input_data["address"]:
            corrected, decision = self._correct_text(
                input_data["address"], "address", tools
            )
            if corrected != input_data["address"]:
                decisions.append(decision)
                input_data["address"] = corrected

        # Check city field
        if "city" in input_data and input_data["city"]:
            corrected, decision = self._correct_text(
                input_data["city"], "city", tools
            )
            if corrected != input_data["city"]:
                decisions.append(decision)
                input_data["city"] = corrected

        if decisions:
            input_data["_decisions"] = decisions

        return input_data

    def _correct_text(self, text: str, field: str, tools: Dict) -> tuple:
        """Correct spelling in text.

        Args:
            text: Text to correct
            field: Field name for logging
            tools: Available tools

        Returns:
            (corrected_text, decision_log)
        """
        if not text:
            return text, None

        text_lower = text.lower()

        # Check exact matches in dictionary
        if text_lower in self.corrections:
            corrected = self.corrections[text_lower]
            return (
                corrected.title() if text[0].isupper() else corrected,
                self.log_decision(
                    f"Corrected {field}: '{text}' → '{corrected}'",
                    f"Found in domain dictionary",
                    confidence=1.0,
                ),
            )

        # Check for close matches using fuzzy matching if tools available
        if "fuzzy_matcher" in tools:
            for wrong, right in self.corrections.items():
                similarity = self._similarity(text_lower, wrong)
                if similarity >= self.threshold:
                    return (
                        right.title() if text[0].isupper() else right,
                        self.log_decision(
                            f"Corrected {field}: '{text}' → '{right}'",
                            f"Fuzzy match (similarity: {similarity:.2f})",
                            confidence=similarity,
                        ),
                    )

        return text, None

    @staticmethod
    def _similarity(s1: str, s2: str) -> float:
        """Simple similarity measure (Levenshtein-like)."""
        if s1 == s2:
            return 1.0
        if len(s1) == 0 or len(s2) == 0:
            return 0.0
        # Simplified: just check prefix + suffix match
        common = sum(1 for a, b in zip(s1, s2) if a == b)
        return common / max(len(s1), len(s2))
