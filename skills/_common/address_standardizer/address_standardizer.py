"""Domain-agnostic address standardization skill."""

import re
from typing import Any, Dict, List, Optional

from skills.base import BaseSkill


class AddressStandardizer(BaseSkill):
    """Expand address abbreviations in configured address fields.

    Runs on any field listed in address_fields config.
    Never touches fields not in address_fields.
    """

    STREET_TYPES = {
        r"\bst\b": "Street", r"\bave\b": "Avenue", r"\bavenue\b": "Avenue",
        r"\bblvd\b": "Boulevard", r"\brd\b": "Road", r"\blane\b": "Lane",
        r"\bln\b": "Lane", r"\bdr\b": "Drive", r"\bct\b": "Court",
        r"\bctr\b": "Center", r"\bpk\b": "Park", r"\bpkwy\b": "Parkway",
        r"\bter\b": "Terrace", r"\bpl\b": "Place", r"\bsq\b": "Square",
    }
    # Single-letter directionals intentionally omitted — too many false positives
    QUADRANTS = {
        r"\bNE\b": "Northeast", r"\bNW\b": "Northwest",
        r"\bSE\b": "Southeast", r"\bSW\b": "Southwest",
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.address_fields: List[str] = self.config.get("address_fields", [])
        self.strip_unit_numbers = self.config.get("strip_unit_numbers", False)

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        self.clear_audit()
        for field in self.address_fields:
            value = input_data.get(field)
            if not value or not isinstance(value, str):
                continue
            standardized = self._standardize(value)
            if standardized != value:
                self.log_decision(
                    f"{field}: '{value}' → '{standardized}'",
                    "address abbreviation expansion",
                    confidence=1.0,
                )
                input_data[field] = standardized
        return input_data

    def _standardize(self, address: str) -> str:
        if not address:
            return address
        if self.strip_unit_numbers:
            address = re.sub(
                r",?\s*(apt|apt\.|unit|unit\.|#)\s*\w+", "", address, flags=re.IGNORECASE
            )
        for pattern, expansion in self.QUADRANTS.items():
            address = re.sub(pattern, expansion, address, flags=re.IGNORECASE)
        for pattern, expansion in self.STREET_TYPES.items():
            address = re.sub(pattern, expansion, address, flags=re.IGNORECASE)
        return " ".join(address.split())
