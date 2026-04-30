"""Data quality triage skill."""

from typing import Any, Dict, Optional
from skills.base import BaseSkill


class DataQualityTriageAgent(BaseSkill):
    """Triage records: done / needs_review / unsalvageable."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.min_confidence_auto = self.config.get("min_confidence_auto_complete", 0.85)
        self.min_confidence_review = self.config.get("min_confidence_agent_review", 0.60)

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        """Triage record quality and route to: done / needs_review / unsalvageable.

        Args:
            input_data: Record dict
            tools: Available tools

        Returns:
            Record with triage decision
        """
        # Evaluate data completeness and confidence
        completeness = self._evaluate_completeness(input_data)
        confidence = self._evaluate_confidence(input_data)
        corrections = len(input_data.get("_agent_decisions", []))

        # Route decision
        triage_result = self._make_triage_decision(completeness, confidence, corrections)

        decision_log = self.log_decision(
            f"Triage: {triage_result['route']} "
            f"(completeness: {completeness:.2f}, confidence: {confidence:.2f})",
            triage_result["reason"],
            confidence=triage_result["confidence"],
        )

        input_data["_triage_route"] = triage_result["route"]
        input_data["_triage_confidence"] = triage_result["confidence"]
        input_data["_triage_completeness"] = completeness
        input_data["_triage_data_confidence"] = confidence

        if "_decisions" not in input_data:
            input_data["_decisions"] = []
        input_data["_decisions"].append(decision_log)

        return input_data

    def _evaluate_completeness(self, record: Dict[str, Any]) -> float:
        """Score how complete the record is (0.0-1.0).

        Args:
            record: Record dict

        Returns:
            Completeness score
        """
        required_fields = ["address", "city", "postal_code", "municipality", "country"]
        present = sum(1 for field in required_fields if record.get(field))
        return present / len(required_fields)

    def _evaluate_confidence(self, record: Dict[str, Any]) -> float:
        """Score confidence in data quality (0.0-1.0).

        Args:
            record: Record dict

        Returns:
            Confidence score
        """
        scores = []

        # Municipality confidence
        if "_municipality_confidence" in record:
            scores.append(record["_municipality_confidence"])

        # Address standardization (proxy: if spell checking happened)
        decisions = record.get("_agent_decisions", [])
        if decisions:
            # More corrections → lower confidence
            correction_count = len([d for d in decisions if "Correct" in d.get("decision", "")])
            scores.append(max(0.5, 1.0 - (correction_count * 0.1)))
        else:
            # No corrections = good sign
            scores.append(0.9)

        # Geographic validation
        if record.get("_geographic_validated"):
            scores.append(0.85)

        # Average of all signals
        return sum(scores) / len(scores) if scores else 0.5

    def _make_triage_decision(self, completeness: float, confidence: float, corrections: int) -> Dict:
        """Make routing decision.

        Args:
            completeness: Completeness score (0.0-1.0)
            confidence: Confidence score (0.0-1.0)
            corrections: Number of corrections made

        Returns:
            Decision dict with route, reason, and confidence
        """
        # Rules:
        # - completeness < 0.7: unsalvageable (missing critical data)
        # - confidence >= min_auto & completeness >= 0.8: done
        # - confidence >= min_review: needs_review
        # - else: unsalvageable

        if completeness < 0.7:
            return {
                "route": "unsalvageable",
                "reason": f"Missing critical fields (completeness: {completeness:.2f})",
                "confidence": 1.0,
            }

        if confidence >= self.min_confidence_auto and completeness >= 0.8:
            return {
                "route": "done",
                "reason": f"High confidence ({confidence:.2f}) and complete ({completeness:.2f})",
                "confidence": min(confidence, completeness),
            }

        if confidence >= self.min_confidence_review:
            return {
                "route": "needs_review",
                "reason": f"Medium confidence ({confidence:.2f}), needs human verification",
                "confidence": confidence,
            }

        return {
            "route": "unsalvageable",
            "reason": f"Low confidence ({confidence:.2f}), not worth processing",
            "confidence": 1.0,
        }
