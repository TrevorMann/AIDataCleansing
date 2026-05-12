"""Geographic validator skill."""

from typing import Any, Dict, Optional
from skills.base import BaseSkill


class GeographicValidator(BaseSkill):
    """Validate geographic coherence (address/postal/municipality/province/country)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.strict_mode = self.config.get("strict_mode", False)

        # Postal code patterns by country
        self.postal_patterns = {
            "CA": r"^[A-Z]\d[A-Z]\s?\d[A-Z]\d$",  # M9L 1H7 or M9L1H7
            "USA": r"^\d{5}(-\d{4})?$",  # 12345 or 12345-6789
            "NL": r"^\d{4}\s?[A-Z]{2}$",  # 1234 AB
            "MX": r"^\d{5}$",  # 12345
            "JP": r"^\d{3}-\d{4}$",  # 123-4567
        }

        # Province/state mapping by country
        self.provinces = {
            "CA": ["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"],
            "USA": ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
                   "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
                   "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
                   "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"],
        }

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        """Validate geographic coherence.

        Args:
            input_data: Record dict
            tools: Available tools

        Returns:
            Record with validation results
        """
        country = input_data.get("country", "CA").upper()
        province = input_data.get("state_province", "").upper()
        postal = input_data.get("postal_code", "").upper()
        municipality = input_data.get("municipality", "")

        any_checks = False

        # Validate postal code format
        if postal:
            any_checks = True
            is_valid_postal = self._validate_postal_format(postal, country)
            if not is_valid_postal:
                self.log_decision(
                    f"Invalid postal format: {postal} for country {country}",
                    "Postal code doesn't match country format",
                    confidence=1.0,
                )
            else:
                self.log_decision(
                    f"Valid postal format: {postal}",
                    f"Matches {country} postal code pattern",
                    confidence=1.0,
                )

        # Validate province exists
        if province and country in self.provinces:
            any_checks = True
            valid_provinces = self.provinces[country]
            if province not in valid_provinces:
                self.log_decision(
                    f"Invalid province: {province} for country {country}",
                    f"Not in list of valid provinces/states for {country}",
                    confidence=1.0,
                )
            else:
                self.log_decision(
                    f"Valid province: {province}",
                    f"Province code valid for {country}",
                    confidence=1.0,
                )

        # Check hierarchy consistency
        if country == "CA" and province == "ON" and municipality:
            any_checks = True
            # Ontario-specific validation
            if municipality in ["Toronto", "Scarborough", "North York", "Etobicoke", "York", "East York"]:
                self.log_decision(
                    f"Consistent hierarchy: {municipality}, ON, Canada",
                    "Municipality matches Ontario jurisdiction",
                    confidence=0.95,
                )
            else:
                self.log_decision(
                    f"Verify municipality: {municipality} in ON",
                    "Check if municipality exists in Ontario",
                    confidence=0.70,
                )

        if any_checks:
            # Store validation summary
            input_data["_geographic_validated"] = True

        return input_data

    def _validate_postal_format(self, postal: str, country: str) -> bool:
        """Validate postal code format for country."""
        import re

        pattern = self.postal_patterns.get(country)
        if not pattern:
            return True  # Unknown country, skip validation

        # Normalize spacing
        postal_normalized = postal.replace(" ", "")
        return bool(re.match(pattern, postal_normalized))
