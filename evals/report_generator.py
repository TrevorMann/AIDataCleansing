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

    def render_notes(self, notes: List[str], title: str) -> str:
        if not notes:
            return ""
        items = "".join(f"<li>{html_lib.escape(n)}</li>" for n in notes)
        return f'<p><strong>{title}:</strong></p><ul style="font-size:13px;">{items}</ul>'

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

        criteria_html = ""
        if result.get("evaluation_criteria"):
            items = "".join(
                f"<li>{html_lib.escape(c)}</li>"
                for c in result["evaluation_criteria"]
            )
            criteria_html = f'<ul style="font-size:13px;">{items}</ul>'

        compliance_notes_html = self.render_notes(metric["compliance_notes"], "Compliance")
        clarity_notes_html = self.render_notes(metric["clarity_notes"], "Clarity")
        trace_html = self.render_trace(result.get("trace", []))

        return f"""
<div style="border:1px solid #ddd;margin:24px 0;padding:18px;border-radius:6px;background:#fff;
            border-left:5px solid {overall_color};">
  <h3 style="margin:0 0 6px 0;color:#333;">{test_id}
    <span style="font-size:13px;font-weight:normal;color:#777;margin-left:10px;">{description}</span>
  </h3>

  <div style="margin:10px 0;">
    {self.render_metric_card(s["accuracy"], "Accuracy")}
    {self.render_metric_card(s["compliance"], "Compliance")}
    {self.render_metric_card(s["clarity"], "Clarity")}
    {self.render_metric_card(s["overall"], "Overall")}
  </div>

  <h4 style="color:#555;">Expected Behavior</h4>
  <p style="font-size:13px;">{expected}</p>

  {f'<h4 style="color:#555;">Evaluation Criteria</h4>{criteria_html}' if criteria_html else ''}

  <h4 style="color:#555;">Input Record</h4>
  <pre style="background:#f9f9f9;padding:10px;border-radius:4px;font-size:12px;overflow-x:auto;">{input_data}</pre>

  <h4 style="color:#555;">LLM Response</h4>
  <pre style="background:#f9f9f9;padding:10px;border-radius:4px;font-size:12px;overflow-x:auto;white-space:pre-wrap;">{llm_response}</pre>

  <h4 style="color:#555;">Extracted Cleaned Record</h4>
  <pre style="background:#f0f8f0;padding:10px;border-radius:4px;font-size:12px;overflow-x:auto;">{extracted}</pre>

  {compliance_notes_html}
  {clarity_notes_html}
  {trace_html}
</div>"""

    def generate_html(self) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Summary cards
        summary_cards = ""
        for cat, stats in self.summary.items():
            summary_cards += self.render_metric_card(stats["mean"], cat.title())

        # Test cases
        cases_html = ""
        for result, metric in zip(self.results, self.metrics):
            cases_html += self.render_test_case(result, metric)

        # Summary table
        rows = ""
        for cat, stats in self.summary.items():
            rows += (
                f'<tr>'
                f'<td>{cat.title()}</td>'
                f'<td style="color:{self._score_color(stats["mean"])};">{int(stats["mean"]*100)}%</td>'
                f'<td>{int(stats["min"]*100)}%</td>'
                f'<td>{int(stats["max"]*100)}%</td>'
                f'</tr>'
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
