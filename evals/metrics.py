"""
Evaluation metrics for prompt assessment.

All scoring is semantic (LLM-judge-driven). No heuristic line counts or keyword checks.

Dimensions (from LLMJudge):
  correctness         — did LLM produce right cleaned values?
  format              — did LLM produce valid structured JSON output?
  instruction_following — did LLM follow system prompt rules?

Overall = mean of all three dimensions.
"""

import json
import re
from typing import List


class MetricsCollector:
    """
    Wraps judge results into per-test-case metric dicts and aggregate stats.

    All accuracy/quality scoring is delegated to LLMJudge.
    This class handles: JSON extraction, summary stats, verdict aggregation.
    """

    @staticmethod
    def extract_json_from_response(response_text: str) -> dict | None:
        """
        Extract cleaned record JSON from LLM response.

        Looks for JSON block (```json ... ```) or last raw JSON object.
        Returns parsed dict or None if not found.
        """
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        try:
            start = response_text.rfind("{")
            if start >= 0:
                return json.loads(response_text[start:])
        except json.JSONDecodeError:
            pass

        return None

    def evaluate_result(self, result: dict, judge_result: dict | None = None) -> dict:
        """
        Build metric dict for a single evaluation result.

        Args:
            result: Output from PromptEvaluator.evaluate_record()
            judge_result: Output from LLMJudge.judge() — if None, all dims = 0.5 placeholder

        Returns dict with:
          extracted_json, scores (correctness/format/instruction_following/overall),
          overall_verdict, criteria_results, judge_reasoning, judge_error
        """
        response = result["llm_response"]
        cleaned_json = self.extract_json_from_response(response)

        if judge_result and judge_result.get("judge_error") is None:
            dims = judge_result.get("dimensions", {})
            correctness           = dims.get("correctness", 0.5)
            format_score          = dims.get("format", 0.5)
            instruction_following = dims.get("instruction_following", 0.5)
            criteria_results  = judge_result.get("criteria_results", [])
            judge_reasoning   = judge_result.get("judge_reasoning", "")
            overall_verdict   = judge_result.get("overall_verdict", "partial")
            judge_error       = None
        else:
            correctness = format_score = instruction_following = 0.5
            criteria_results  = []
            judge_reasoning   = judge_result.get("judge_error", "Judge not run") if judge_result else "Judge not run"
            overall_verdict   = "partial"
            judge_error       = judge_result.get("judge_error") if judge_result else "Judge not run"

        overall = (correctness + format_score + instruction_following) / 3

        return {
            "test_case_id": result["test_case_id"],
            "extracted_json": cleaned_json,
            "scores": {
                "correctness":           round(correctness, 3),
                "format":                round(format_score, 3),
                "instruction_following": round(instruction_following, 3),
                "overall":               round(overall, 3),
            },
            "overall_verdict":   overall_verdict,
            "criteria_results":  criteria_results,
            "judge_reasoning":   judge_reasoning,
            "judge_error":       judge_error,
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
            judge_results: Aligned list of LLMJudge result dicts.
                           Pass None to skip judge (all dims = 0.5 placeholder).
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

        categories = ["correctness", "format", "instruction_following", "overall"]
        summary = {}
        for cat in categories:
            scores = [m["scores"][cat] for m in metrics]
            summary[cat] = {
                "mean": round(sum(scores) / len(scores), 3),
                "min":  round(min(scores), 3),
                "max":  round(max(scores), 3),
                "count": len(scores),
            }

        verdicts = [m.get("overall_verdict", "partial") for m in metrics]
        summary["verdicts"] = {
            "pass":    verdicts.count("pass"),
            "partial": verdicts.count("partial"),
            "fail":    verdicts.count("fail"),
        }

        return summary
