"""
Generate HTML reports for prompt evaluation results.

Shows per-test-case: input, expected behavior, LLM response, scores.
"""

import html as html_lib
import json
from datetime import datetime
from pathlib import Path
from typing import List


class HTMLReportGenerator:
    """Generate interactive HTML report from evaluation results + metrics."""

    def __init__(self, results: List[dict], metrics: List[dict], summary: dict):
        """
        Args:
            results: Output from PromptEvaluator.evaluate_dataset()
            metrics: Output from MetricsCollector.evaluate_all()
            summary: Output from MetricsCollector.summary_stats()
        """
        self.results = results
        self.metrics = metrics
        self.summary = summary

    def _score_color(self, score: float) -> str:
        if score >= 0.8:
            return "#27ae60"  # green
        if score >= 0.6:
            return "#e67e22"  # orange
        return "#c0392b"      # red

    def render_metric_card(self, score: float, label: str) -> str:
        color = self._score_color(score)
        pct = int(score * 100)
        return (
            f'<div style="display:inline-block;margin:8px 12px;text-align:center;">'
            f'<div style="font-size:28px;font-weight:bold;color:{color};">{pct}%</div>'
            f'<div style="font-size:11px;color:#666;">{label}</div>'
            f'</div>'
        )

    def render_trace(self, trace: List[str]) -> str:
        lines = "\n".join(html_lib.escape(line) for line in trace)
        return (
            f'<details style="margin:10px 0;">'
            f'<summary style="cursor:pointer;font-weight:bold;color:#555;">▶ Evaluation Trace</summary>'
            f'<pre style="background:#f4f4f4;padding:12px;border-radius:4px;font-size:12px;overflow-x:auto;">'
            f'{lines}</pre></details>'
        )

    def render_criteria_results(self, criteria_results: list, judge_reasoning: str, judge_error: str | None) -> str:
        """Render per-criterion pass/fail from LLM judge."""
        if judge_error:
            return (
                f'<div style="background:#fff3cd;padding:10px;border-radius:4px;'
                f'border-left:4px solid #ffc107;font-size:13px;">'
                f'⚠️ Judge error: {html_lib.escape(judge_error)}</div>'
            )

        if not criteria_results:
            return ""

        rows = ""
        for cr in criteria_results:
            passed = cr.get("pass", False)
            score = cr.get("score", 0.0)
            criterion = html_lib.escape(str(cr.get("criterion", "")))
            reason = html_lib.escape(str(cr.get("reason", "")))
            icon = "✅" if passed else "❌"
            pct = int(score * 100)
            color = self._score_color(score)
            rows += (
                f'<tr>'
                f'<td style="width:30px;text-align:center;">{icon}</td>'
                f'<td style="font-size:12px;">{criterion}</td>'
                f'<td style="width:50px;text-align:center;font-weight:bold;color:{color};">{pct}%</td>'
                f'<td style="font-size:12px;color:#666;">{reason}</td>'
                f'</tr>'
            )

        reasoning_html = ""
        if judge_reasoning:
            reasoning_html = (
                f'<p style="font-size:12px;color:#555;font-style:italic;margin:8px 0 0;">'
                f'Judge: {html_lib.escape(judge_reasoning)}</p>'
            )

        return (
            f'<h4 style="color:#555;">Judge Verdict — Per Criterion</h4>'
            f'<table style="width:100%;border-collapse:collapse;margin:8px 0;">'
            f'<tr style="background:#f0f0f0;">'
            f'<th style="padding:6px;font-size:11px;width:30px;"></th>'
            f'<th style="padding:6px;font-size:11px;text-align:left;">Criterion</th>'
            f'<th style="padding:6px;font-size:11px;width:50px;">Score</th>'
            f'<th style="padding:6px;font-size:11px;text-align:left;">Reason</th>'
            f'</tr>'
            f'{rows}'
            f'</table>'
            f'{reasoning_html}'
        )

    def render_test_case(self, result: dict, metric: dict) -> str:
        test_id = html_lib.escape(result["test_case_id"])
        description = html_lib.escape(result["description"])
        input_data = html_lib.escape(json.dumps(result["input"], indent=2))
        llm_response = html_lib.escape(result["llm_response"])
        expected = html_lib.escape(result["expected_behavior"])

        s = metric["scores"]
        overall_color = self._score_color(s["overall"])

        extracted = (
            html_lib.escape(json.dumps(metric["extracted_json"], indent=2))
            if metric["extracted_json"]
            else "❌ Could not extract JSON from response"
        )

        verdict = metric.get("overall_verdict", "partial")
        verdict_badge = {
            "pass":    '<span style="background:#27ae60;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;">PASS</span>',
            "partial": '<span style="background:#e67e22;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;">PARTIAL</span>',
            "fail":    '<span style="background:#c0392b;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;">FAIL</span>',
        }.get(verdict, "")

        criteria_judge_html = self.render_criteria_results(
            metric.get("criteria_results", []),
            metric.get("judge_reasoning", ""),
            metric.get("judge_error"),
        )

        trace_html = self.render_trace(result.get("trace", []))

        return f"""
<div style="border:1px solid #ddd;margin:24px 0;padding:18px;border-radius:6px;background:#fff;
            border-left:5px solid {overall_color};">
  <h3 style="margin:0 0 6px 0;color:#333;">{test_id} {verdict_badge}
    <span style="font-size:13px;font-weight:normal;color:#777;margin-left:10px;">{description}</span>
  </h3>

  <div style="margin:10px 0;">
    {self.render_metric_card(s["correctness"], "Correctness")}
    {self.render_metric_card(s["format"], "Format")}
    {self.render_metric_card(s["instruction_following"], "Instruction Following")}
    {self.render_metric_card(s["overall"], "Overall")}
  </div>

  <h4 style="color:#555;">Expected Behavior</h4>
  <p style="font-size:13px;">{expected}</p>

  {criteria_judge_html}

  <h4 style="color:#555;">Input Record</h4>
  <pre style="background:#f9f9f9;padding:10px;border-radius:4px;font-size:12px;overflow-x:auto;">{input_data}</pre>

  <details style="margin:10px 0;">
    <summary style="cursor:pointer;font-weight:bold;color:#555;">▶ LLM Response</summary>
    <pre style="background:#f9f9f9;padding:10px;border-radius:4px;font-size:12px;overflow-x:auto;white-space:pre-wrap;">{llm_response}</pre>
  </details>

  <h4 style="color:#555;">Extracted Cleaned Record</h4>
  <pre style="background:#f0f8f0;padding:10px;border-radius:4px;font-size:12px;overflow-x:auto;">{extracted}</pre>

  {trace_html}
</div>"""

    def generate_html(self) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Summary cards (skip "verdicts")
        summary_cards = ""
        for cat, stats in self.summary.items():
            if cat == "verdicts":
                continue
            summary_cards += self.render_metric_card(stats["mean"], cat.title())

        # Test cases
        cases_html = ""
        for result, metric in zip(self.results, self.metrics):
            cases_html += self.render_test_case(result, metric)

        # Summary table (skip "verdicts" — different shape)
        rows = ""
        for cat, stats in self.summary.items():
            if cat == "verdicts":
                continue
            rows += (
                f'<tr>'
                f'<td>{cat.title()}</td>'
                f'<td style="color:{self._score_color(stats["mean"])};">{int(stats["mean"]*100)}%</td>'
                f'<td>{int(stats["min"]*100)}%</td>'
                f'<td>{int(stats["max"]*100)}%</td>'
                f'</tr>'
            )

        # Verdict breakdown row
        v = self.summary.get("verdicts", {})
        if v:
            rows += (
                f'<tr style="background:#f9f9f9;">'
                f'<td>Verdicts</td>'
                f'<td colspan="3" style="font-size:12px;">'
                f'✅ Pass: {v.get("pass", 0)} &nbsp;|&nbsp; '
                f'🟠 Partial: {v.get("partial", 0)} &nbsp;|&nbsp; '
                f'❌ Fail: {v.get("fail", 0)}'
                f'</td></tr>'
            )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Prompt Evaluation Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
           margin: 0; padding: 20px; background: #f5f5f5; color: #333; }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    h1 {{ color: #222; margin-bottom: 4px; }}
    h2 {{ color: #444; border-bottom: 2px solid #ddd; padding-bottom: 8px; }}
    h3, h4 {{ margin: 12px 0 6px; }}
    pre {{ margin: 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; font-size: 13px; }}
    th {{ background: #f0f0f0; }}
    details summary::-webkit-details-marker {{ color: #888; }}
  </style>
</head>
<body>
<div class="container">
  <h1>Prompt Evaluation Report</h1>
  <p style="color:#666;font-size:13px;">Generated: {timestamp} &nbsp;|&nbsp;
     Test cases: {len(self.results)}</p>

  <h2>Summary</h2>
  <div style="background:#fff;padding:12px;border-radius:6px;border:1px solid #ddd;margin-bottom:16px;">
    {summary_cards}
  </div>

  <table>
    <tr><th>Metric</th><th>Mean</th><th>Min</th><th>Max</th></tr>
    {rows}
  </table>

  <h2>Test Cases</h2>
  {cases_html}

  <hr style="border:none;border-top:1px solid #ddd;margin:30px 0;">
  <p style="font-size:11px;color:#aaa;">
    Generated by prompt evaluation harness. See JSON results files for raw data.
  </p>
</div>
</body>
</html>"""

    def save_html(self, output_path: str):
        """Save HTML report to file."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.generate_html())
        print(f"✅ HTML report saved to {output_path}")
