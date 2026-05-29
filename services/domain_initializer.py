"""Phase 0 domain initialization — table discovery, keyword scoring, registry management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

SYSTEM_TABLES: frozenset[str] = frozenset({
    # Framework metadata / caching tables
    "column_metadata",
    "spell_corrections",
    "query_pattern_memory",
    "plan_cache",
    "municipality_lookup_cache",
    "source_registry",
    # Additional infrastructure tables that exist in the project
    "audit_log",
    "flags",
    "column_profiles",
    "geo_boundary_reference",
    "city_municipality_map",
    "fsa_municipality_mapping",
})

DOMAIN_ENTITY_WORDS: dict[str, list[str]] = {
    "sports_ticketing": [
        "event", "ticket", "customer", "fan", "venue", "team",
        "seat", "section", "purchase", "account", "booking",
        "price", "sport", "game", "match", "player",
    ],
    "real_estate": [
        "property", "listing", "address", "postal", "municipality",
        "province", "city", "agent", "broker", "price", "mls",
    ],
    "_generic": ["record", "data", "entry", "item", "entity", "profile"],
}

_DEFAULT_REGISTRY_PATH = Path(__file__).parent.parent / "data" / "domain_registry.json"


class DomainInitializer:
    """Manages domain table registration in domain_registry.json."""

    def __init__(self, domain: str, registry_path: Optional[Path] = None):
        self.domain = domain
        self.registry_path = registry_path or _DEFAULT_REGISTRY_PATH

    def _load(self) -> dict:
        with self.registry_path.open() as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        with self.registry_path.open("w") as f:
            json.dump(data, f, indent=2)

    def get_registered_tables(self) -> Optional[list[str]]:
        """Return registered tables for this domain, or None if not registered."""
        data = self._load()
        domain_entry = data.get("domains", {}).get(self.domain)
        if domain_entry is None:
            return None
        return domain_entry.get("tables")

    def register_tables(self, tables: list[str]) -> None:
        """Write (or overwrite) the tables list for this domain in the registry."""
        data = self._load()
        data.setdefault("domains", {}).setdefault(self.domain, {})["tables"] = tables
        self._save(data)

    def unregister_tables(self) -> bool:
        """Remove this domain's `tables` entry from the registry.

        If the domain entry has no other keys afterwards (i.e. it was created
        solely by initialization), the whole entry is removed. Rich entries
        (with prompt_module, skills_path, etc.) keep everything except `tables`.
        Returns True if a `tables` entry was actually removed.
        """
        data = self._load()
        domain_entry = data.get("domains", {}).get(self.domain)
        if domain_entry is None:
            return False
        removed = domain_entry.pop("tables", None) is not None
        if not domain_entry:
            del data["domains"][self.domain]
        self._save(data)
        return removed

    def get_all_db_tables(self, conn) -> list[str]:
        """Return all user tables currently in the DB (Postgres first, SQLite fallback)."""
        postgres_failed = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                )
                return [row["table_name"] for row in cur.fetchall()]
        except Exception:  # noqa: BLE001 — expected on SQLite connections
            postgres_failed = True

        if postgres_failed:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                return [row[0] for row in cur.fetchall()]

        return []  # unreachable, but satisfies type checkers

    def diff_tables(self, conn) -> list[str]:
        """Return tables in DB that are not registered and not system tables.
        Preserves order from get_all_db_tables (database order, typically by name)."""
        registered = set(self.get_registered_tables() or [])
        all_tables = self.get_all_db_tables(conn)
        return [t for t in all_tables if t not in registered and t not in SYSTEM_TABLES]

    def score_tables(self, tables: list[str]) -> list[tuple[str, int]]:
        """Score tables by keyword overlap with domain entity words. Descending order."""
        words = set(
            DOMAIN_ENTITY_WORDS.get(self.domain, [])
            + DOMAIN_ENTITY_WORDS["_generic"]
            + self.domain.replace("_", " ").split()
        )
        result = []
        for table in tables:
            tokens = set(table.replace("_", " ").split())
            # Also consider singular forms (strip trailing 's') for better matching
            # e.g. "tickets" → also tries "ticket"
            expanded = tokens | {t.rstrip("s") for t in tokens if t.endswith("s") and len(t) > 2}
            score = len(expanded & words)
            result.append((table, score))
        return sorted(result, key=lambda x: -x[1])
