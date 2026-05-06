"""
Evaluation metrics for prompt assessment.

Scores:
- Accuracy: Did LLM produce expected output? (placeholder — requires human review)
- Compliance: Did LLM follow the rules (e.g., no guessing, documents uncertainty)?
- Clarity: Is reasoning transparent?
"""

import json
import re
from typing import Any, List, Tuple


class MetricsCollector:
    """
    Analyzes LLM response against expected behavior.

    For each test case:
    1. Extract cleaned record JSON from LLM response
    2. Score compliance (did it follow the rules?)
    3. Score clarity (is reasoning transparent?)
    4. Identify issues for HTML report
    """

    @staticmethod
    def extract_json_from_response(response_text: str) -> dict | None:
        """
        Extract cleaned record JSON from LLM response.

        Looks for JSON block (```json ... ```) or last raw JSON object.
        Returns parsed dict or None if not found.
        """
        # Try fenced code block first
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try last JSON object in response
        try:
            start = response_text.rfind("{")
            if start >= 0:
                return json.loads(response_text[start:])
        except json.JSONDecodeError:
            pass

        return None

    @staticmethod
    def score_compliance(response_text: str, rules: List[str]) -> Tuple[float, List[str]]:
        """
        Check if LLM followed the rules (no guessing, documents uncertainty).

        Returns (score 0.0-1.0, compliance_notes list).
        """
        notes = []
        lower = response_text.lower()

        # Rule: document uncertainty explicitly
        if "unable to determine" in lower or "unclear" in lower or "cannot determine" in lower:
            notes.append("✅ Explicitly documents uncertainty")
        else:
            notes.append("⚠️  No explicit uncertainty markers (may be OK for clear-cut records)")

        # Rule: provide reasoning
        if "because" in lower or "reason" in lower or "since" in lower:
            notes.append("✅ Provides reasoning for decisions")
        else:
            notes.append("⚠️  Could provide more reasoning")

        # Rule: preserve original values when uncertain
        if "leave" in lower or "unchanged" in lower or "original" in lower or "preserve" in lower:
            notes.append("✅ Mentions preserving original values")
        else:
            notes.append("⚠️  No mention of preserving uncertain fields")

        passing = len([n for n in notes if n.startswith("✅")])
        return passing / max(len(notes), 1), notes

    @staticmethod
    def score_clarity(response_text: str) -> Tuple[float, List[str]]:
        """
        Score how clear and traceable the reasoning is.

        Returns (score 0.0-1.0, clarity_notes list).
        """
        notes = []
        lines = response_text.split("\n")

        # Length check — detailed responses are more traceable
        if len(lines) > 10:
            notes.append("✅ Response is detailed (field-by-field analysis likely)")
        else:
            notes.append("⚠️  Response is brief (may lack field-by-field detail)")

        # Field-by-field structure
        if "name:" in response_text.lower() or "field" in response_text.lower():
            notes.append("✅ Appears to analyze fields individually")
        else:
            notes.append("⚠️  Not obviously field-by-field")

        # Multiple explanations
        explanation_words = response_text.lower().count("because") + response_text.lower().count("reason")
        if explanation_words > 1:
            notes.append("✅ Multiple explanations for decisions")
        else:
            notes.append("⚠️  Limited explanations found")

        # JSON output present
        if "```" in response_text or response_text.rfind("{") >= 0:
            notes.append("✅ Includes structured JSON output")
        else:
            notes.append("⚠️  No structured JSON output found")

        passing = len([n for n in notes if n.startswith("✅")])
        return passing / max(len(notes), 1), notes

    def evaluate_result(self, result: dict) -> dict:
        """
        Analyze single evaluation result.

        Returns dict with scores, extracted JSON, compliance/clarity notes, issues list.
        """
        response = result["llm_response"]

        # Extract cleaned record
        cleaned_json = self.extract_json_from_response(response)

        # Score compliance
        compliance_score, compliance_notes = self.score_compliance(
            response,
            result.get("evaluation_criteria", [])
        )

        # Score clarity
        clarity_score, clarity_notes = self.score_clarity(response)

        # Accuracy placeholder — requires human or LLM-as-judge to score properly
        accuracy_score = 0.5

        return {
            "test_case_id": result["test_case_id"],
            "extracted_json": cleaned_json,
            "scores": {
                "accuracy": accuracy_score,
                "compliance": compliance_score,
                "clarity": clarity_score,
                "overall": (accuracy_score + compliance_score + clarity_score) / 3,
            },
            "compliance_notes": compliance_notes,
            "clarity_notes": clarity_notes,
            "issues": [
                issue for issue in clarity_notes + compliance_notes
                if issue.startswith("⚠️")
            ],
        }

    def evaluate_all(self, results: List[dict]) -> List[dict]:
        """Evaluate all results, return metrics for each."""
        return [self.evaluate_result(r) for r in results]

    @staticmethod
    def summary_stats(metrics: List[dict]) -> dict:
        """Calculate aggregate stats across all test cases."""
        if not metrics:
            return {}

        categories = ["accuracy", "compliance", "clarity", "overall"]
        summary = {}
        for cat in categories:
            scores = [m["scores"][cat] for m in metrics]
            summary[cat] = {
                "mean": sum(scores) / len(scores),
                "min": min(scores),
                "max": max(scores),
                "count": len(scores),
            }
        return summary
