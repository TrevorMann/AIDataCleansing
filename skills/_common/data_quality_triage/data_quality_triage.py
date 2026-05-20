"""Domain-agnostic data quality triage skill."""

from typing import Any, Dict, List, Optional

from skills.base import BaseSkill


class DataQualityTriageAgent(BaseSkill):
    """Triage records: done / needs_review / unsalvageable.

    Config keys:
      required_fields          list[str]  Fields that must be present for completeness.
                                          Default [] → completeness always 1.0.
      confidence_signal_keys   list[str]  Record keys whose float values feed into
                                          min() confidence. Default [].
      validated_signal_keys    list[str]  Record keys that are boolean flags; True
                                          contributes a fixed 0.85 signal. Default [].
      min_confidence_auto_complete float  Route 'done' above this. Default 0.85.
      min_confidence_agent_review  float  Route 'needs_review' above this. Default 0.60.
    """

    _VALIDATED_FIXED_SCORE = 0.85
    _BASELINE_CONFIDENCE = 0.9

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.required_fields: List[str] = self.config.get("required_fields", [])
        self.confidence_signal_keys: List[str] = self.config.get("confidence_signal_keys", [])
        self.validated_signal_keys: List[str] = self.config.get("validated_signal_keys", [])
        self.min_confidence_auto = self.config.get("min_confidence_auto_complete", 0.85)
        self.min_confidence_review = self.config.get("min_confidence_agent_review", 0.60)

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        completeness = self._evaluate_completeness(input_data)
        confidence = self._evaluate_confidence(input_data)
        triage = self._make_triage_decision(completeness, confidence)

        self.log_decision(
            f"Triage: {triage['route']} "
            f"(completeness={completeness:.2f}, confidence={confidence:.2f})",
            triage["reason"],
            confidence=triage["confidence"],
        )

        input_data["_triage_route"] = triage["route"]
        input_data["_triage_confidence"] = triage["confidence"]
        input_data["_triage_completeness"] = completeness
        input_data["_triage_data_confidence"] = confidence
        return input_data

    def _evaluate_completeness(self, record: Dict[str, Any]) -> float:
        if not self.required_fields:
            return 1.0
        present = sum(1 for f in self.required_fields if record.get(f))
        return present / len(self.required_fields)

    def _evaluate_confidence(self, record: Dict[str, Any]) -> float:
        scores = [self._BASELINE_CONFIDENCE]
        for key in self.confidence_signal_keys:
            if key in record:
                scores.append(float(record[key]))
        for key in self.validated_signal_keys:
            if record.get(key) is True:
                scores.append(self._VALIDATED_FIXED_SCORE)
        return min(scores)

    def _make_triage_decision(self, completeness: float, confidence: float) -> Dict[str, Any]:
        if completeness < 0.7:
            return {
                "route": "unsalvageable",
                "reason": f"Missing critical fields (completeness={completeness:.2f})",
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
                "reason": f"Medium confidence ({confidence:.2f}), needs verification",
                "confidence": confidence,
            }
        return {
            "route": "unsalvageable",
            "reason": f"Low confidence ({confidence:.2f})",
            "confidence": 1.0,
        }
