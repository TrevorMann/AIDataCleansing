"""
Entry point for prompt evaluation workflow.

Usage:
    # Evaluate a hand-crafted dataset
    python evals/run.py --dataset general_cleaning --output-json --output-html

    # Auto-generate dataset from base prompt, then evaluate
    python evals/run.py --generate --num-cases 10 --output-html

    # List available datasets
    python evals/run.py --list-datasets
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

DATASETS_DIR = Path("evals/datasets")
RESULTS_DIR = Path("evals/results")


def list_datasets():
    """Print available datasets."""
    datasets = sorted(DATASETS_DIR.glob("*.json"))
    if not datasets:
        print("No datasets found in evals/datasets/")
        return
    print(f"Available datasets ({len(datasets)}):")
    for p in datasets:
        try:
            d = json.loads(p.read_text())
            name = d.get("metadata", {}).get("name", p.stem)
            desc = d.get("metadata", {}).get("description", "")
            count = len(d.get("test_cases", []))
            print(f"  {p.stem:35s}  {count:3d} cases  {desc[:60]}")
        except Exception:
            print(f"  {p.stem}  (could not read metadata)")


def generate_dataset(num_cases: int, output_name: str) -> Path:
    """Auto-generate dataset from base prompt + schema."""
    from evals.dataset_generator import DatasetGenerator
    from prompts import build_system_prompt
    from schema_discovery import format_schema_for_prompt

    logger.info(f"Generating {num_cases} test cases from base prompt...")
    schema = format_schema_for_prompt("data/cleaning.db")
    prompt = build_system_prompt(sub=None, schema=schema)

    gen = DatasetGenerator(prompt, schema=schema, dataset_name=output_name)
    dataset = gen.generate_dataset(num_cases=num_cases)

    output_path = DATASETS_DIR / f"{output_name}.json"
    gen.save_dataset(dataset, str(output_path))
    logger.info(f"✅ Generated dataset: {output_path} ({len(dataset['test_cases'])} cases)")
    return output_path


def run_evaluation(dataset_path: Path, output_json: bool, output_html: bool) -> None:
    """Run full evaluation pipeline: evaluate → metrics → report."""
    from evals.prompt_evaluator import PromptEvaluator
    from evals.metrics import MetricsCollector
    from evals.report_generator import HTMLReportGenerator

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    base_filename = f"{timestamp}-{dataset_path.stem}"

    # ── Phase 1: Evaluate ──────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("PHASE 1: EVALUATION  (send each test case to LLM)")
    logger.info("=" * 70)
    logger.info(f"Dataset: {dataset_path}")

    evaluator = PromptEvaluator()
    results = evaluator.evaluate_dataset(str(dataset_path))
    logger.info(f"✅ Completed {len(results)} evaluations\n")

    # ── Phase 1.5: LLM Judge ──────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("PHASE 1.5: LLM JUDGE  (semantic per-criterion scoring)")
    logger.info("=" * 70)

    from evals.llm_judge import LLMJudge
    collector = MetricsCollector()
    extracted_jsons = [
        collector.extract_json_from_response(r["llm_response"]) for r in results
    ]
    judge = LLMJudge()
    judge_results = judge.judge_all(results, extracted_jsons)
    logger.info("")

    # ── Phase 2: Metrics ───────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("PHASE 2: METRICS  (score responses)")
    logger.info("=" * 70)

    metrics = collector.evaluate_all(results, judge_results=judge_results)
    summary = collector.summary_stats(metrics)

    logger.info(f"✅ Scored {len(metrics)} results")
    for cat, stats in summary.items():
        if cat == "verdicts":
            v = stats
            logger.info(f"   {'Verdicts':12s}: pass={v['pass']} partial={v['partial']} fail={v['fail']}")
        else:
            logger.info(f"   {cat.title():12s}: {stats['mean']:.1%}")
    logger.info("")

    # ── Phase 3: Output ────────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("PHASE 3: REPORT GENERATION")
    logger.info("=" * 70)

    if output_json:
        json_path = RESULTS_DIR / f"{base_filename}-results.json"
        evaluator.save_results(str(json_path))
        logger.info(f"✅ Raw results : {json_path}")

        metrics_path = RESULTS_DIR / f"{base_filename}-metrics.json"
        metrics_path.write_text(
            json.dumps({"metrics": metrics, "summary": summary,
                        "timestamp": datetime.now().isoformat()}, indent=2)
        )
        logger.info(f"✅ Metrics     : {metrics_path}")

    if output_html:
        html_path = RESULTS_DIR / f"{base_filename}-report.html"
        gen = HTMLReportGenerator(results, metrics, summary)
        gen.save_html(str(html_path))
        logger.info(f"✅ HTML report : {html_path}")

    logger.info("")
    logger.info("=" * 70)
    logger.info("WORKFLOW COMPLETE")
    logger.info("=" * 70)
    logger.info(f"Output directory: {RESULTS_DIR.absolute()}")


def main():
    parser = argparse.ArgumentParser(
        description="Prompt evaluation workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python evals/run.py --dataset general_cleaning --output-html
  python evals/run.py --generate --num-cases 10 --output-html
  python evals/run.py --list-datasets
        """.strip()
    )

    # Dataset selection
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dataset",
        help="Name of dataset in evals/datasets/ (without .json)"
    )
    group.add_argument(
        "--generate",
        action="store_true",
        help="Auto-generate dataset from base prompt before evaluating"
    )
    group.add_argument(
        "--list-datasets",
        action="store_true",
        help="List available datasets and exit"
    )

    # Generation options
    parser.add_argument(
        "--num-cases",
        type=int,
        default=10,
        help="Number of test cases to generate (with --generate, default: 10)"
    )
    parser.add_argument(
        "--generated-name",
        default="auto_generated",
        help="Filename for generated dataset (default: auto_generated)"
    )

    # Output options
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Save raw results + metrics as JSON files"
    )
    parser.add_argument(
        "--output-html",
        action="store_true",
        default=True,
        help="Generate HTML report (default: True)"
    )
    parser.add_argument(
        "--output-dir",
        default="evals/results",
        help="Directory for output files (default: evals/results)"
    )

    args = parser.parse_args()

    global RESULTS_DIR
    RESULTS_DIR = Path(args.output_dir)

    # ── List datasets ──────────────────────────────────────────────────────────
    if args.list_datasets:
        list_datasets()
        sys.exit(0)

    # ── Determine dataset path ─────────────────────────────────────────────────
    if args.generate:
        dataset_path = generate_dataset(
            num_cases=args.num_cases,
            output_name=args.generated_name
        )
    elif args.dataset:
        dataset_path = DATASETS_DIR / f"{args.dataset}.json"
        if not dataset_path.exists():
            logger.error(f"Dataset not found: {dataset_path}")
            logger.error("Run with --list-datasets to see available datasets.")
            sys.exit(1)
    else:
        # Default to general_cleaning if it exists
        default = DATASETS_DIR / "general_cleaning.json"
        if default.exists():
            dataset_path = default
            logger.info(f"No dataset specified, using default: {default}")
        else:
            logger.error("No dataset specified. Use --dataset or --generate.")
            parser.print_help()
            sys.exit(1)

    # ── Run evaluation ─────────────────────────────────────────────────────────
    run_evaluation(
        dataset_path=dataset_path,
        output_json=args.output_json,
        output_html=args.output_html,
    )


if __name__ == "__main__":
    main()
