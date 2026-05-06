"""Seeder: load real_estate column metadata into column_metadata table."""

from pathlib import Path
from typing import Any

import yaml

from seeders.base import Seeder


class ColumnMetadataSeeder(Seeder):
    name = "column_metadata"
    domain = "real_estate"
    target_table = "column_metadata"
    source_tag = "domain_seed"
    schema_required = ["column_metadata"]

    def fetch(self) -> Any:
        seed_yaml = self.config.get(
            "seed_yaml", "data/seeds/real_estate/column_metadata.yaml"
        )
        path = Path(seed_yaml)
        if not path.exists():
            raise FileNotFoundError(f"column_metadata seed not found: {path}")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def parse(self, payload: Any) -> list:
        domain = payload.get("domain", self.domain)
        rows = []
        for table_name, cols in payload.get("tables", {}).items():
            for entry in cols:
                rows.append({
                    "domain":      domain,
                    "table_name":  table_name,
                    "column_name": entry["column"],
                    "description": entry.get("description", "").strip(),
                })
        return rows

    def upsert(self, conn, rows: list) -> int:
        cursor = conn.cursor()
        count = 0
        for row in rows:
            cursor.execute(
                """
                INSERT INTO column_metadata (domain, table_name, column_name, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (domain, table_name, column_name)
                DO UPDATE SET description = excluded.description
                """,
                (row["domain"], row["table_name"], row["column_name"], row["description"]),
            )
            count += 1
        conn.commit()
        return count
