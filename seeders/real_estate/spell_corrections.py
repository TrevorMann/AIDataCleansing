"""Seeder: load spell corrections CSV into spell_corrections table."""

import csv
from pathlib import Path
from seeders.base import Seeder


class SpellCorrectionsSeeder(Seeder):
    name = "spell_corrections"
    domain = "real_estate"
    target_table = "spell_corrections"
    source_tag = "manual_seed"
    schema_required = ["spell_corrections"]

    def fetch(self):
        seed_csv = self.config.get("seed_csv", "data/seeds/real_estate/spell_corrections.csv")
        path = Path(seed_csv)
        if not path.exists():
            raise FileNotFoundError(f"Seed CSV not found: {path}")
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def parse(self, payload) -> list:
        rows = []
        for row in payload:
            rows.append({
                "wrong": row["wrong"].strip().lower(),
                "domain": self.domain,
                "right": row["right"].strip().lower(),
                "source": row.get("source", "manual_seed"),
                "confidence": float(row.get("confidence", 1.0)),
            })
        return rows

    def upsert(self, conn, rows: list) -> int:
        from db.upsert import bulk_insert_ignore
        return bulk_insert_ignore(conn, "spell_corrections", rows, ["wrong", "domain"])
