"""Address standardization skill for real estate data."""

import re
from typing import Any, Dict, Optional
from skills.base import BaseSkill


class AddressStandardizer(BaseSkill):
    """Standardize address format (strip units, expand abbreviations, fix directionals)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.strip_unit_numbers = self.config.get("strip_unit_numbers", False)

        # Street type abbreviations to expand
        self.street_types = {
            r"\bst\b": "Street",
            r"\bave\b": "Avenue",
            r"\bavenue\b": "Avenue",
            r"\bblvd\b": "Boulevard",
            r"\brd\b": "Road",
            r"\blane\b": "Lane",
            r"\bln\b": "Lane",
            r"\bdr\b": "Drive",
            r"\bct\b": "Court",
            r"\bctr\b": "Center",
            r"\bpk\b": "Park",
            r"\bpkwy\b": "Parkway",
            r"\bter\b": "Terrace",
            r"\bpl\b": "Place",
            r"\bsq\b": "Square",
        }

        # Quadrant abbreviations only — single-letter directionals (N/E/S/W) are
        # intentionally NOT expanded because \bN\b matches "N" anywhere in a token
        # sequence and produces false expansions (e.g. "123 Doe N Main" → "North Main").
        self.quadrants = {
            r"\bNE\b": "Northeast",
            r"\bNW\b": "Northwest",
            r"\bSE\b": "Southeast",
            r"\bSW\b": "Southwest",
        }

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        """Standardize address field.

        Args:
            input_data: Record dict
            tools: Available tools

        Returns:
            Record with standardized address
        """
        decisions = []

        if "address" in input_data and input_data["address"]:
            original = input_data["address"]
            standardized = self._standardize(original)

            if standardized != original:
                decisions.append(
                    self.log_decision(
                        f"Standardized address: '{original}' → '{standardized}'",
                        "Applied address standardization rules",
                        confidence=1.0,
                    )
                )
                input_data["address"] = standardized

        if decisions:
            input_data["_decisions"] = decisions

        return input_data

    def _standardize(self, address: str) -> str:
        """Apply standardization rules to address."""
        if not address:
            return address

        # Strip unit numbers if requested
        if self.strip_unit_numbers:
            # Remove apt/unit numbers: "123 Main St, Apt 456" → "123 Main St"
            address = re.sub(r",?\s*(apt|apt\.|unit|unit\.|#)\s*\w+", "", address, flags=re.IGNORECASE)

        # Expand quadrant abbreviations FIRST (before street types, to avoid token interference)
        for abbr, full in self.quadrants.items():
            address = re.sub(abbr, full, address, flags=re.IGNORECASE)

        # Expand street type abbreviations (case-insensitive)
        for abbr, full in self.street_types.items():
            address = re.sub(abbr, full, address, flags=re.IGNORECASE)

        # Normalize spacing
        address = " ".join(address.split())

        return address
