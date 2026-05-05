"""Categorize ticket products: full_season / half_season / individual / voucher."""

import re
from typing import Any, Dict, Optional
from skills.base import BaseSkill

_CATEGORY_PATTERNS = [
    ("full_season", re.compile(r"\bfull[\s-]?season\b|\b82[\s-]?game\b|\b38[\s-]?game\b", re.I)),
    ("half_season", re.compile(r"\bhalf[\s-]?season\b|\b41[\s-]?game\b|\b19[\s-]?game\b", re.I)),
    ("voucher", re.compile(r"\bvoucher\b|\bflex\b|\bcredit\b|\bgift\b", re.I)),
    ("individual", re.compile(r"\bsingle[\s-]?game\b|\bindividual\b|\b1[\s-]?game\b|\bone[\s-]?game\b", re.I)),
]


class TicketProductCategorizer(BaseSkill):
    """Categorize ticket product type from description/name."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "sports_ticketing"

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        product_name = input_data.get("product_name", "") or input_data.get("ticket_type", "")
        if not product_name:
            return input_data

        existing_category = input_data.get("ticket_category")
        if existing_category:
            return input_data  # already categorized

        for category, pattern in _CATEGORY_PATTERNS:
            if pattern.search(product_name):
                input_data["ticket_category"] = category
                input_data.setdefault("_decisions", []).append(
                    self.log_decision(
                        f"Categorized ticket: '{product_name}' → {category}",
                        f"Pattern match: {pattern.pattern[:40]}",
                        confidence=0.85,
                    )
                )
                return input_data

        # Default: individual if no match
        input_data["ticket_category"] = "individual"
        input_data.setdefault("_decisions", []).append(
            self.log_decision(
                f"Defaulted ticket to individual: '{product_name}'",
                "No pattern matched",
                confidence=0.50,
            )
        )
        return input_data
