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
        fuzzy_matcher = tools.get("fuzzy_matcher") if tools else None
        if fuzzy_matcher:
            best_match = None
            best_score = 0.0
            # Build candidate pairs: (comparison_target, correction_result)
            # Include both misspelling keys and correct forms as comparison targets
            unique_rights = {r for r in self.corrections.values()}
            candidates = list(self.corrections.items())
            for right in unique_rights:
                candidates.append((right, right))
            for wrong, right in candidates:
                # For prefix matches (e.g. "scarb" is prefix of "scarborough"), use
                # Dice-coefficient-style score: 2*len(prefix)/(len(prefix)+len(target))
                if len(text_lower) < len(wrong) and wrong.startswith(text_lower):
                    similarity = 2 * len(text_lower) / (len(text_lower) + len(wrong))
                else:
                    similarity = fuzzy_matcher.compare(text_lower, wrong)
                if similarity >= self.threshold and similarity > best_score:
                    best_match = right
                    best_score = similarity
            if best_match:
                return (
                    best_match.title() if text[0].isupper() else best_match,
                    self.log_decision(
                        f"Corrected {field}: '{text}' → '{best_match}'",
                        f"Fuzzy match (similarity: {best_score:.2f})",
                        confidence=best_score,
                    ),
                )

        return text, None
