"""Municipality authority agent skill."""

from typing import Any, Dict, Optional
from skills.base import BaseSkill


class MunicipalityAuthorityAgent(BaseSkill):
    """Resolve municipality authority (neighborhood vs legal jurisdiction)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.trust_postal = self.config.get("trust_postal_over_name", True)
        self.escalate_threshold = self.config.get("escalate_confidence_threshold", 0.60)

        # FSA to municipality mapping for Toronto
        self.fsa_to_municipality = {
            "M1A": "Scarborough",
            "M1B": "Scarborough",
            "M1C": "Scarborough",
            "M1E": "Scarborough",
            "M1G": "Scarborough",
            "M1H": "Scarborough",
            "M1J": "Scarborough",
            "M1K": "Scarborough",
            "M1L": "Scarborough",
            "M1M": "Scarborough",
            "M1N": "Scarborough",
            "M1P": "Scarborough",
            "M1R": "Scarborough",
            "M1S": "Scarborough",
            "M1T": "Scarborough",
            "M1V": "Scarborough",
            "M1W": "Scarborough",
            "M1X": "Scarborough",
            "M2H": "North York",
            "M2J": "North York",
            "M2K": "North York",
            "M2L": "North York",
            "M2M": "North York",
            "M2N": "North York",
            "M2P": "North York",
            "M2R": "North York",
            "M3A": "North York",
            "M3B": "North York",
            "M3C": "North York",
            "M3H": "North York",
            "M3J": "North York",
            "M3K": "North York",
            "M3L": "North York",
            "M3M": "North York",
            "M3N": "North York",
            "M4A": "Toronto",
            "M4B": "Toronto",
            "M4C": "Toronto",
            "M4E": "Toronto",
            "M4G": "Toronto",
            "M4H": "Toronto",
            "M4J": "Toronto",
            "M4K": "Toronto",
            "M4L": "Toronto",
            "M4M": "Toronto",
            "M4N": "Toronto",
            "M4P": "Toronto",
            "M4R": "Toronto",
            "M4S": "Toronto",
            "M4T": "Toronto",
            "M4V": "Toronto",
            "M4W": "Toronto",
            "M4X": "Toronto",
            "M4Y": "Toronto",
            "M5A": "Toronto",
            "M5B": "Toronto",
            "M5C": "Toronto",
            "M5E": "Toronto",
            "M5G": "Toronto",
            "M5H": "Toronto",
            "M5J": "Toronto",
            "M5K": "Toronto",
            "M5L": "Toronto",
            "M5M": "Toronto",
            "M5N": "Toronto",
            "M5P": "Toronto",
            "M5R": "Toronto",
            "M5S": "Toronto",
            "M5T": "Toronto",
            "M5V": "Toronto",
            "M5W": "Toronto",
            "M5X": "Toronto",
            "M5Y": "Toronto",
            "M5Z": "Toronto",
            "M6A": "Toronto",
            "M6B": "Toronto",
            "M6C": "Toronto",
            "M6E": "Toronto",
            "M6G": "Toronto",
            "M6H": "Toronto",
            "M6J": "Toronto",
            "M6K": "Toronto",
            "M6L": "Toronto",
            "M6M": "Toronto",
            "M6N": "Toronto",
            "M6P": "Toronto",
            "M6R": "Toronto",
            "M6S": "Toronto",
            "M7A": "Toronto",
            "M7R": "Etobicoke",
            "M7Y": "Etobicoke",
            "M8A": "Etobicoke",
            "M8B": "Etobicoke",
            "M8C": "Etobicoke",
            "M8E": "Etobicoke",
            "M8G": "Etobicoke",
            "M8H": "Etobicoke",
            "M8J": "Etobicoke",
            "M8K": "Etobicoke",
            "M8P": "Etobicoke",
            "M8V": "Etobicoke",
            "M8W": "Etobicoke",
            "M8X": "Etobicoke",
            "M8Y": "Etobicoke",
            "M8Z": "Etobicoke",
            "M9A": "Etobicoke",
            "M9B": "Etobicoke",
            "M9C": "Etobicoke",
            "M9L": "North York",
            "M9M": "North York",
            "M9N": "North York",
            "M9P": "North York",
            "M9R": "Etobicoke",
            "M9S": "Etobicoke",
            "M9T": "Etobicoke",
            "M9V": "Etobicoke",
            "M9W": "Etobicoke",
        }

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        """Resolve municipality authority.

        Args:
            input_data: Record dict
            tools: Available tools

        Returns:
            Record with municipality resolved and decision logged
        """
        decisions = []

        # Extract postal code and municipality
        postal_code = input_data.get("postal_code", "")
        upstream_municipality = input_data.get("municipality", "")

        # Extract FSA (first 3 chars of postal code)
        fsa = postal_code[:3].upper() if postal_code else ""

        if not fsa:
            # No postal code, can't resolve
            return input_data

        # Look up municipality from FSA
        fsa_municipality = self.fsa_to_municipality.get(fsa)

        if not fsa_municipality:
            # Unknown FSA
            decisions.append(
                self.log_decision(
                    f"Unknown FSA: {fsa}",
                    "FSA not in mapping, cannot resolve municipality",
                    confidence=0.0,
                )
            )
            input_data["_decisions"] = decisions
            return input_data

        # Compare upstream municipality with FSA-resolved municipality
        if not upstream_municipality:
            # No upstream municipality, use FSA result
            decisions.append(
                self.log_decision(
                    f"Resolved municipality: {fsa_municipality} (from FSA {fsa})",
                    "No upstream municipality provided, used FSA lookup",
                    confidence=0.95,
                )
            )
            input_data["municipality"] = fsa_municipality
            input_data["_municipality_confidence"] = 0.95
        elif upstream_municipality.lower() == fsa_municipality.lower():
            # Match
            decisions.append(
                self.log_decision(
                    f"Confirmed municipality: {fsa_municipality}",
                    f"Upstream '{upstream_municipality}' matches FSA {fsa} lookup",
                    confidence=1.0,
                )
            )
            input_data["municipality"] = fsa_municipality
            input_data["_municipality_confidence"] = 1.0
        else:
            # Conflict: upstream says one thing, FSA says another
            if self.trust_postal:
                # Trust FSA (postal code)
                decisions.append(
                    self.log_decision(
                        f"Resolved conflict: {fsa_municipality} (FSA trusted over upstream)",
                        f"FSA {fsa} → {fsa_municipality}, but upstream was '{upstream_municipality}'. "
                        f"Trusting postal code.",
                        confidence=0.85,
                    )
                )
                input_data["municipality"] = fsa_municipality
                input_data["_municipality_confidence"] = 0.85
            else:
                # Trust upstream municipality
                decisions.append(
                    self.log_decision(
                        f"Conflict detected: upstream '{upstream_municipality}' vs FSA '{fsa_municipality}'",
                        f"Trusting upstream municipality, but flagging for review",
                        confidence=0.60,
                    )
                )
                input_data["_municipality_confidence"] = 0.60

        if decisions:
            input_data["_decisions"] = decisions

        return input_data
