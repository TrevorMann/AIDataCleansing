"""CLI: generate skeleton for a new domain.

Creates: skills/<domain>/, seeders/<domain>/, data/seeds/<domain>/
with template stubs ready to fill in.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TEMPLATES = Path(__file__).parent.parent / "templates" / "domain"


def scaffold(domain: str):
    targets = {
        f"skills/{domain}": ["__init__.py", "skills.yaml"],
        f"seeders/{domain}": ["__init__.py", "manifest.yaml"],
        f"data/seeds/{domain}": ["README.md"],
    }

    for dir_path, files in targets.items():
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        for fname in files:
            tmpl_name = fname + ".tmpl"
            tmpl = TEMPLATES / tmpl_name
            dst = Path(dir_path) / fname

            if dst.exists():
                print(f"  SKIP (exists): {dst}")
                continue

            if tmpl.exists():
                content = tmpl.read_text().replace("{{DOMAIN}}", domain)
            else:
                content = _default_content(fname, domain)

            dst.write_text(content)
            print(f"  CREATE: {dst}")


def _default_content(fname: str, domain: str) -> str:
    if fname == "__init__.py":
        return ""
    if fname == "skills.yaml":
        return f"""domain: {domain}
version: 0.1

config:
  fuzzy_match_threshold: 0.85

skills:
  # Add domain skills here. See skills/real_estate/skills.yaml for reference.
  # example_skill:
  #   class: skills.{domain}.example_skill.example_skill.ExampleSkill
  #   skill_doc: skills/{domain}/example_skill/skill.md
  #   tools: []
  #   config: {{}}
  #   cost: low
  #   latency_estimate_ms: 100
  #   depends_on: []
"""
    if fname == "manifest.yaml":
        return f"""domain: {domain}
description: "Seeders for {domain} domain"
schema_migrations: []
seeders: []
  # - name: example_seeder
  #   class: seeders.{domain}.example.ExampleSeeder
  #   enabled: true
  #   license: "..."
"""
    if fname == "README.md":
        return f"""# {domain.replace("_", " ").title()} Seed Data

Add seed CSV files and documentation here.
Run: `python scripts/init_data.py --domain {domain} --dry-run` to preview.
"""
    return ""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scaffold skeleton for a new domain.")
    ap.add_argument("--domain", required=True, help="New domain name (e.g. sports_ticketing)")
    args = ap.parse_args()

    print(f"Scaffolding domain: {args.domain}")
    scaffold(args.domain)
    print(f"\nDone. Next steps:")
    print(f"  1. Edit skills/{args.domain}/skills.yaml — declare your skills")
    print(f"  2. Edit seeders/{args.domain}/manifest.yaml — declare your seeders")
    print(f"  3. Drop seed CSVs in data/seeds/{args.domain}/")
    print(f"  4. python scripts/init_data.py --domain {args.domain} --dry-run")
