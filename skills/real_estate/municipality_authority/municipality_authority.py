"""Municipality authority agent skill — DB-backed via MunicipalityResolver."""

from typing import Any, Dict, Optional
from skills.base import BaseSkill


class MunicipalityAuthorityAgent(BaseSkill):
    """Resolve municipality via DB (MunicipalityResolver), not hardcoded dict."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.trust_postal = self.config.get("trust_postal_over_name", True)
        self.escalate_threshold = self.config.get("escalate_confidence_threshold", 0.60)
        self.conn = self.config.get("pg_conn")

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        postal_code = input_data.get("postal_code", "")
        upstream = input_data.get("municipality", "")

        if not postal_code:
            return input_data

        # Build listing dict in MunicipalityResolver format
        listing = {
            "street": input_data.get("address", ""),
            "municipality": upstream,
            "postal_code": postal_code,
            "province": input_data.get("state_province", "ON"),
            "year": input_data.get("year", 2024),
        }

        if self.conn:
            try:
                from cleaning.municipality_resolver import MunicipalityResolver
                resolver = MunicipalityResolver(self.conn)
                result = resolver.resolve(listing)
                resolved = result.get("normalized_municipality", "")
                confidence = result.get("confidence_score", 0.0)
                status = result.get("normalization_status", "unknown")

                if resolved:
                    input_data["municipality"] = resolved
                    input_data["_municipality_confidence"] = confidence
                    self.log_decision(
                        f"Resolved municipality: {resolved} (via DB, status={status})",
                        f"MunicipalityResolver confidence={confidence:.2f}",
                        confidence=confidence,
                    )
                else:
                    # DB miss — flag for web search enrichment
                    fsa = postal_code[:3].upper().replace(" ", "")
                    input_data["_unknown_fsa"] = fsa
                    input_data["_municipality_confidence"] = 0.0
                    self.log_decision(
                        f"Unknown FSA: {fsa}",
                        "MunicipalityResolver returned no result — needs web_search enrichment",
                        confidence=0.0,
                    )
            except Exception as e:
                # DB unavailable — flag and continue
                input_data["_municipality_confidence"] = 0.0
                self.log_decision(
                    "Municipality resolution failed",
                    f"DB error: {str(e)[:100]}",
                    confidence=0.0,
                )
        else:
            # No conn — warn and flag
            input_data["_municipality_confidence"] = 0.0
            self.log_decision(
                "Municipality resolution skipped",
                "No pg_conn configured — run init_data.py to seed DB",
                confidence=0.0,
            )

        return input_data
