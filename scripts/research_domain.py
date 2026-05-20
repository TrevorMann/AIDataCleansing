"""Interactive domain researcher: asks questions, calls LLM, writes seed files.

Usage:
  python scripts/research_domain.py --domain sports_ticketing
  python scripts/research_domain.py --domain sports_ticketing --dry-run
  python scripts/research_domain.py --domain sports_ticketing --force   # overwrite existing
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_client_factory import create_client
from seeders.domain_researcher import DomainResearcher


def _ask(question, default="") -> str:
    """Prompt user and return their answer. Enter with no input returns default."""
    if question.hint:
        print(f"  hint: {question.hint}")
    raw = input(f"\n{question.prompt}\n> ").strip()
    return raw if raw else default


def _print_preview(bundle) -> None:
    print("\n" + "─" * 60)
    print("PREVIEW — generated seed content")
    print("─" * 60)

    print(f"\nSpell corrections ({len(bundle.spell_corrections)}):")
    for sc in bundle.spell_corrections[:8]:
        print(f"  {sc.wrong!r:20s} → {sc.right!r}  (confidence={sc.confidence:.2f})")
    if len(bundle.spell_corrections) > 8:
        print(f"  ... and {len(bundle.spell_corrections) - 8} more")

    print(f"\nQuery packs ({len(bundle.query_packs)} gap types):")
    for qp in bundle.query_packs:
        print(f"  [{qp.gap_type}]")
        for q in qp.seed_queries[:2]:
            print(f"    {q}")

    print(f"\nColumn descriptions ({len(bundle.column_descriptions)} columns):")
    for cd in bundle.column_descriptions[:6]:
        print(f"  {cd.column_name:20s} ({cd.data_type}) — {cd.description[:60]}")
    if len(bundle.column_descriptions) > 6:
        print(f"  ... and {len(bundle.column_descriptions) - 6} more")

    print("─" * 60)


def run(domain: str, dry_run: bool, force: bool) -> None:
    researcher = DomainResearcher(domain=domain)

    print(f"\n{'='*60}")
    print(f"  Domain researcher — {domain}")
    print(f"{'='*60}")
    print("\nAnswer the following questions to help the LLM generate accurate")
    print("seed data for your domain. Press Enter to skip optional questions.\n")

    answers = {}
    for question in researcher.questions:
        answers[question.key] = _ask(question)

    print("\nConnecting to LLM to generate seed content...")
    try:
        client, backend, model = create_client()
    except EnvironmentError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    bundle = researcher.research(answers, llm_client=client, model=model)

    _print_preview(bundle)

    if dry_run:
        print("\n[dry-run] No files written.")
        return

    confirm = input("\nWrite these files? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    output_dir = Path("data/seeds") / domain
    output_dir.mkdir(parents=True, exist_ok=True)

    written = researcher.write_seeds(bundle, output_dir=output_dir, dry_run=False, force=force)

    if written:
        print("\nWritten:")
        for path in written:
            print(f"  {path}")
        print(f"\nNext steps:")
        print(f"  python scripts/init_data.py --domain {domain} --dry-run")
        print(f"  python scripts/annotate_domain.py --domain {domain}")
    else:
        print("\nNo files written (all already exist — use --force to overwrite).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="LLM-driven domain researcher: generates seed data from Q&A."
    )
    ap.add_argument("--domain", required=True, help="Domain name (e.g. sports_ticketing)")
    ap.add_argument("--dry-run", action="store_true", help="Show preview without writing files")
    ap.add_argument("--force", action="store_true", help="Overwrite existing seed files")
    args = ap.parse_args()

    run(domain=args.domain, dry_run=args.dry_run, force=args.force)
