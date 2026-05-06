"""
Evaluation metrics for prompt assessment.

Scores:
- Accuracy: LLM-as-judge score per evaluation criterion (semantic, not heuristic)
- Compliance: Did LLM follow structural rules (provide reasoning, handle uncertainty)?
- Clarity: Is reasoning transparent and well-structured?

Accuracy is driven by LLMJudge (evals/llm_judge.py).
Compliance and Clarity are fast heuristic checks on response structure.
"""

import json
import re
from typing import List, Tuple


class MetricsCollector:
    """
    Analyzes LLM response against expected behavior.

    For each test case:
    1. Extract cleaned record JSON from LLM response
    2. Score accuracy via LLM judge result (semantic per-criterion scoring)
    3. Score compliance (structural: provides reasoning, handles uncertainty when needed)
    4. Score clarity (structural: field-by-field, explanations, JSON output)
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
    def score_compliance(response_text: str) -> Tuple[float, List[str]]:
        """
        Check structural compliance — does response provide reasoning?

        Returns (score 0.0-1.0, notes list).
        Note: uncertainty/preservation checks removed — LLM judge handles
        whether those were required given the specific test case context.
        """
        notes = []
        lower = response_text.lower()

        # Provides explicit reasoning
        if "because" in lower or "reason" in lower or "since" in lower or "therefore" in lower:
            notes.append("✅ Provides reasoning for decisions")
        else:
            notes.append("⚠️  Limited or no explicit reasoning found")

        # Explains what was changed vs left unchanged
        changed_words = ("changed", "corrected", "updated", "standardized", "formatted", "expanded")
        if any(w in lower for w in changed_words):
            notes.append("✅ Describes what was changed")
        else:
            notes.append("⚠️  Does not describe changes made")

        # Structured output (JSON at the end)
        if "```" in response_text or response_text.rfind("{") >= 0:
            notes.append("✅ Includes structured JSON output")
        else:
            notes.append("⚠️  No structured JSON output found")

        passing = len([n for n in notes if n.startswith("✅")])
        return passing / max(len(notes), 1), notes

    @staticmethod
    def score_clarity(response_text: str) -> Tuple[float, List[str]]:
        """
        Score how clear and traceable the reasoning is.

        Returns (score 0.0-1.0, notes list).
        """
        notes = []
        lines = response_text.split("\n")

        # Length: detailed responses are more traceable
        if len(lines) > 10:
            notes.append("✅ Response is detailed (field-by-field analysis likely)")
        else:
            notes.append("⚠️  Response is brief (may lack field-by-field detail)")

        # Field-by-field breakdown
        if "name:" in response_text.lower() or "field" in response_text.lower():
            notes.append("✅ Appears to analyze fields individually")
        else:
            notes.append("⚠️  Not obviously field-by-field")

        # Multiple distinct explanations
        explanation_count = (
            response_text.lower().count("because")
            + response_text.lower().count("reason")
            + response_text.lower().count("therefore")
        )
        if explanation_count > 1:
            notes.append("✅ Multiple explanations for decisions")
        else:
            notes.append("⚠️  Limited explanations found")

        passing = len([n for n in notes if n.startswith("✅")])
        return passing / max(len(notes), 1), notes

    def evaluate_result(self, result: dict, judge_result: dict | None = None) -> dict:
        """
        Analyze single evaluation result.

        Args:
            result: Output from PromptEvaluator.evaluate_record()
            judge_result: Output from LLMJudge.judge() — if None, accuracy is 0.5 placeholder

        Returns dict with:
        - extracted_json: cleaned record produced by LLM
        - scores: accuracy (judge-driven), compliance, clarity, overall
        - criteria_results: per-criterion pass/fail/reason from judge
        - judge_reasoning: judge's summary of quality
        - compliance_notes / clarity_notes: structural check results
        - issues: warnings to surface in HTML report
        """
        response = result["llm_response"]

        # Extract cleaned record
        cleaned_json = self.extract_json_from_response(response)

        # Accuracy from LLM judge (semantic per-criterion scoring)
        if judge_result and judge_result.get("judge_error") is None:
            accuracy_score = judge_result["accuracy_score"]
            criteria_results = judge_result.get("criteria_results", [])
            judge_reasoning = judge_result.get("judge_reasoning", "")
            overall_verdict = judge_result.get("overall_verdict", "partial")
            judge_error = None
        else:
            accuracy_score = 0.5
            criteria_results = []
            judge_reasoning = judge_result.get("judge_error", "Judge not run") if judge_result else "Judge not run"
            overall_verdict = "partial"
            judge_error = judge_result.get("judge_error") if judge_result else "Judge not run"

        # Structural compliance check
        compliance_score, compliance_notes = self.score_compliance(response)

        # Clarity check
        clarity_score, clarity_notes = self.score_clarity(response)

        overall_score = (accuracy_score + compliance_score + clarity_score) / 3

        return {
            "test_case_id": result["test_case_id"],
            "extracted_json": cleaned_json,
            "scores": {
                "accuracy": accuracy_score,
                "compliance": compliance_score,
                "clarity": clarity_score,
                "overall": overall_score,
            },
            "overall_verdict": overall_verdict,
            "criteria_results": criteria_results,
            "judge_reasoning": judge_reasoning,
            "judge_error": judge_error,
            "compliance_notes": compliance_notes,
            "clarity_notes": clarity_notes,
            "issues": [
                issue for issue in clarity_notes + compliance_notes
                if issue.startswith("⚠️")
            ],
        }

    def evaluate_all(
        self,
        results: List[dict],
        judge_results: List[dict] | None = None,
    ) -> List[dict]:
        """
        Evaluate all results, return metrics for each.

        Args:
            results: List of PromptEvaluator result dicts
            judge_results: List of LLMJudge result dicts (aligned by index).
                           Pass None to skip judge (accuracy = 0.5 placeholder).
        """
        if judge_results is None:
            judge_results = [None] * len(results)
        return [
            self.evaluate_result(r, j)
            for r, j in zip(results, judge_results)
        ]

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

        # Verdict breakdown
        verdicts = [m.get("overall_verdict", "partial") for m in metrics]
        summary["verdicts"] = {
            "pass": verdicts.count("pass"),
            "partial": verdicts.count("partial"),
            "fail": verdicts.count("fail"),
        }

        return summary
