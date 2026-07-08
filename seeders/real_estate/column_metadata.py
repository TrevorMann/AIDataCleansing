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
        import json
        domain = payload.get("domain", self.domain)
        rows = []
        for table_name, cols in payload.get("tables", {}).items():
            for entry in cols:
                gd = entry.get("gap_detection")
                rows.append({
                    "domain":        domain,
                    "table_name":    table_name,
                    "column_name":   entry["column"],
                    "description":   entry.get("description", "").strip(),
                    "gap_detection": json.dumps(gd) if gd else None,
                })
        return rows

    def upsert(self, conn, rows: list) -> int:
        from db.upsert import bulk_upsert

        return bulk_upsert(
            conn,
            "column_metadata",
            rows,
            conflict_cols=["domain", "table_name", "column_name"],
            update_cols=["description", "gap_detection"],
        )
