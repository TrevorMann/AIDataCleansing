"""Seeder registry: discover and run domain seeders from manifest."""

import yaml
from importlib import import_module
from pathlib import Path
from typing import Optional


class SeederRegistry:
    """Load and run domain seeders defined in manifest.yaml."""

    def __init__(self, domain: str):
        self.domain = domain
        manifest_path = Path(__file__).parent / domain / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest for domain: {domain}")
        with open(manifest_path) as f:
            self.manifest = yaml.safe_load(f)
        self.seeders = self._load_seeders()

    def _load_seeders(self) -> list:
        seeders = []
        for entry in self.manifest.get("seeders", []):
            if not entry.get("enabled", True):
                continue
            class_path = entry["class"]
            mod_name, cls_name = class_path.rsplit(".", 1)
            cls = getattr(import_module(mod_name), cls_name)
            instance = cls(config=entry.get("config", {}))
            seeders.append(instance)
        return seeders

    def run_all(self, conn, only: Optional[list] = None, dry_run: bool = False) -> dict:
        """Run all enabled seeders. Returns {seeder_name: rows_added | None}."""
        results = {}
        for s in self.seeders:
            if only and s.name not in only:
                continue
            if dry_run:
                print(f"[{self.domain}/{s.name}] DRY — target={s.target_table}, source={s.source_tag}")
                results[s.name] = -1
                continue
            try:
                count = s.run(conn)
                results[s.name] = count
                print(f"[{self.domain}/{s.name}] → {count} rows ({s.source_tag})")
            except Exception as e:
                print(f"[{self.domain}/{s.name}] FAILED: {e}")
                results[s.name] = None
        return results
