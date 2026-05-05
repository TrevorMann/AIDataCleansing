"""Seeder: load query packs YAML into query_pattern_memory + source_registry."""

from pathlib import Path
from seeders.base import Seeder


class QueryPackSeeder(Seeder):
    name = "query_packs"
    domain = "real_estate"
    target_table = "query_pattern_memory"
    source_tag = "seed_yaml"
    schema_required = ["query_pattern_memory", "source_registry"]

    def fetch(self):
        import yaml
        packs_yaml = self.config.get("packs_yaml", "data/seeds/real_estate/query_packs.yaml")
        path = Path(packs_yaml)
        if not path.exists():
            raise FileNotFoundError(f"Query packs YAML not found: {path}")
        with open(path) as f:
            return yaml.safe_load(f)

    def parse(self, payload) -> list:
        rows = []
        for gap_type, spec in payload.get("gap_types", {}).items():
            for query_template in spec.get("seed_queries", []):
                rows.append({
                    "domain": self.domain,
                    "gap_type": gap_type,
                    "query_template": query_template,
                })
        return rows

    def upsert(self, conn, rows: list) -> int:
        if not rows:
            return 0
        params = [(r["domain"], r["gap_type"], r["query_template"]) for r in rows]
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO query_pattern_memory (domain, gap_type, query_template)
                VALUES (%s, %s, %s)
                ON CONFLICT (domain, gap_type, query_template) DO NOTHING
                """,
                params,
            )
        conn.commit()
        return len(rows)
