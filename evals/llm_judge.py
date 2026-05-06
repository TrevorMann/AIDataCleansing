"""
LLM-as-judge for prompt evaluation accuracy scoring.

Sends each test case's response to a second LLM call that evaluates:
  - Per-criterion pass/fail with reasoning
  - Overall accuracy score (0.0-1.0)
  - Whether expected behavior was followed

This replaces heuristic accuracy scoring with semantic understanding.
The judge sees: input record + expected behavior + evaluation criteria + LLM response.
"""

import json
import logging
import re
from typing import Any

from llm_client_factory import create_client, build_system_param, build_message_kwargs, log_usage

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM = """You are an impartial evaluator assessing the quality of a data cleaning response.

You will be given:
1. The original dirty data record
2. The expected behavior (what should happen)
3. Evaluation criteria (specific checkpoints)
4. The actual LLM response to evaluate

Your job: judge whether the response met each criterion.

Rules for judging:
- Judge based on WHAT the response actually did, not whether it used specific words
- A criterion about uncertainty only applies if the record is actually ambiguous
- A criterion about preserving values only matters if there were values that couldn't be cleaned
- Be strict but fair — partial credit is OK (use score between 0.0 and 1.0)
- Focus on correctness, not style

You MUST respond with valid JSON only, in this exact structure:
{
  "criteria_results": [
    {
      "criterion": "<exact criterion text>",
      "pass": true,
      "score": 1.0,
      "reason": "<one sentence explaining the verdict>"
    }
  ],
  "accuracy_score": 0.85,
  "overall_verdict": "pass",
  "judge_reasoning": "<2-3 sentences summarizing overall quality>"
}

Where:
- pass: true if criterion is met, false if not
- score: 0.0 to 1.0 for this criterion (1.0 = fully met, 0.5 = partially, 0.0 = failed)
- overall_verdict: "pass" (≥0.8), "partial" (0.5-0.8), or "fail" (<0.5)
- accuracy_score: weighted average of criterion scores (0.0 to 1.0)

Output ONLY valid JSON. No preamble, no explanation outside the JSON."""


class LLMJudge:
    """
    Judges LLM responses against evaluation criteria using a second LLM call.

    Provides semantic accuracy scoring that understands context —
    a response that correctly handles a clear-cut record is not penalized
    for not mentioning uncertainty when none was needed.
    """

    def __init__(self):
        """Initialize judge with its own LLM client."""
        self.client, self.backend, self.model = create_client()
        self._judge_call_count = 0

    def _build_judge_prompt(
        self,
        input_record: dict,
        expected_behavior: str,
        evaluation_criteria: list[str],
        llm_response: str,
        cleaned_json: dict | None,
    ) -> str:
        """Build the user message for the judge."""
        criteria_list = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(evaluation_criteria))
        cleaned_str = json.dumps(cleaned_json, indent=2) if cleaned_json else "(no JSON extracted)"

        return f"""ORIGINAL RECORD:
{json.dumps(input_record, indent=2)}

EXPECTED BEHAVIOR:
{expected_behavior}

EVALUATION CRITERIA:
{criteria_list}

ACTUAL LLM RESPONSE:
{llm_response}

EXTRACTED CLEANED RECORD:
{cleaned_str}

Judge this response against each criterion and return your assessment as JSON."""

    def judge(self, result: dict, cleaned_json: dict | None) -> dict:
        """
        Judge a single evaluation result.

        Args:
            result: Output from PromptEvaluator.evaluate_record()
            cleaned_json: Cleaned JSON extracted from response (or None)

        Returns:
            Judge result dict with:
            - criteria_results: list of {criterion, pass, score, reason}
            - accuracy_score: float 0.0-1.0
            - overall_verdict: "pass" | "partial" | "fail"
            - judge_reasoning: summary string
            - judge_tokens: token usage for this judge call
            - judge_error: error string if judge call failed (None if OK)
        """
        self._judge_call_count += 1
        test_id = result["test_case_id"]

        criteria = result.get("evaluation_criteria", [])
        if not criteria:
            logger.warning(f"[judge] {test_id}: no evaluation_criteria — skipping judge")
            return _fallback_result(test_id, "No evaluation criteria defined")

        prompt = self._build_judge_prompt(
            input_record=result["input"],
            expected_behavior=result["expected_behavior"],
            evaluation_criteria=criteria,
            llm_response=result["llm_response"],
            cleaned_json=cleaned_json,
        )

        system_param = build_system_param(self.backend, _JUDGE_SYSTEM)
        message_kwargs = build_message_kwargs(self.backend)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_param,
                messages=[{"role": "user", "content": prompt}],
                **message_kwargs
            )
            log_usage(self.backend, response.usage)

            raw_text = next(
                (b.text for b in response.content if hasattr(b, "text")),
                ""
            )

            judge_result = _parse_judge_response(raw_text, criteria)
            judge_result["judge_tokens"] = {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0),
                "cache_read": getattr(response.usage, "cache_read_input_tokens", 0),
            }
            judge_result["judge_error"] = None
            logger.debug(
                f"[judge] {test_id}: accuracy={judge_result['accuracy_score']:.2f} "
                f"verdict={judge_result['overall_verdict']}"
            )
            return judge_result

        except Exception as e:
            logger.error(f"[judge] {test_id}: failed — {e}")
            result = _fallback_result(test_id, str(e))
            result["judge_tokens"] = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
            return result

    def judge_all(self, results: list[dict], extracted_jsons: list[dict | None]) -> list[dict]:
        """Judge all results. Returns list of judge result dicts."""
        judgments = []
        total = len(results)
        for i, (result, cleaned) in enumerate(zip(results, extracted_jsons), 1):
            logger.info(
                f"  [judge {i}/{total}] {result['test_case_id']}..."
            )
            judgments.append(self.judge(result, cleaned))
        logger.info(f"✅ Judge complete ({self._judge_call_count} calls)")
        return judgments

    @property
    def call_count(self) -> int:
        return self._judge_call_count


def _parse_judge_response(raw_text: str, criteria: list[str]) -> dict:
    """
    Parse JSON from judge response.

    Tries direct parse, then regex extraction.
    Falls back to uniform 0.5 scores if unparseable.
    """
    # Try direct parse
    try:
        data = json.loads(raw_text.strip())
        return _validate_judge_data(data, criteria)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object with regex
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return _validate_judge_data(data, criteria)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse judge response: {raw_text[:100]}...")
    return _fallback_result("unknown", f"Parse failed. Raw: {raw_text[:200]}")


def _validate_judge_data(data: dict, criteria: list[str]) -> dict:
    """Ensure required fields exist; fill defaults if missing."""
    criteria_results = data.get("criteria_results", [])

    # If judge didn't return per-criterion results, synthesize from accuracy_score
    if not criteria_results and criteria:
        score = float(data.get("accuracy_score", 0.5))
        criteria_results = [
            {"criterion": c, "pass": score >= 0.5, "score": score, "reason": "Inferred from overall score"}
            for c in criteria
        ]

    # Recompute accuracy_score from criterion scores for consistency
    if criteria_results:
        accuracy = sum(r.get("score", 0.5) for r in criteria_results) / len(criteria_results)
    else:
        accuracy = float(data.get("accuracy_score", 0.5))

    return {
        "criteria_results": criteria_results,
        "accuracy_score": round(accuracy, 3),
        "overall_verdict": data.get("overall_verdict", _verdict(accuracy)),
        "judge_reasoning": data.get("judge_reasoning", ""),
    }


def _fallback_result(test_id: str, reason: str) -> dict:
    """Return neutral 0.5 result when judge call fails."""
    return {
        "criteria_results": [],
        "accuracy_score": 0.5,
        "overall_verdict": "partial",
        "judge_reasoning": f"Judge unavailable: {reason}",
        "judge_error": reason,
    }


def _verdict(score: float) -> str:
    if score >= 0.8:
        return "pass"
    if score >= 0.5:
        return "partial"
    return "fail"
