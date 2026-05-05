"""Normalize sports event names to canonical team names."""

import re
from typing import Any, Dict, Optional
from skills.base import BaseSkill

_TEAM_ALIASES = {
    "leafs": "toronto maple leafs",
    "maple leafs": "toronto maple leafs",
    "toronto leafs": "toronto maple leafs",
    "habs": "montreal canadiens",
    "canadiens": "montreal canadiens",
    "sens": "ottawa senators",
    "senators": "ottawa senators",
    "jets": "winnipeg jets",
    "flames": "calgary flames",
    "oilers": "edmonton oilers",
    "canucks": "vancouver canucks",
    "jays": "toronto blue jays",
    "blue jays": "toronto blue jays",
    "raptors": "toronto raptors",
}


def _normalize_team(name: str) -> str:
    key = name.lower().strip()
    return _TEAM_ALIASES.get(key, name.strip())


class EventNormalizer(BaseSkill):
    """Normalize event names to canonical team names. DB-backed aliases override hardcoded defaults."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "sports_ticketing"
        self.conn = self.config.get("pg_conn")
        self._aliases = dict(_TEAM_ALIASES)
        if self.conn:
            self._aliases.update(self._load_aliases_from_db())

    def _load_aliases_from_db(self) -> dict:
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT alias, canonical FROM team_name_aliases WHERE domain = 'sports_ticketing'")
                return {row[0].lower(): row[1] for row in cur.fetchall()}
        except Exception:
            return {}

    def _normalize_name(self, name: str) -> str:
        key = name.lower().strip()
        return self._aliases.get(key, name.strip())

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        event_name = input_data.get("event_name", "")
        if not event_name:
            return input_data

        # Pattern: "Team A vs Team B" or "Team A @ Team B"
        m = re.match(r"^(.+?)\s+(?:vs\.?|@|at)\s+(.+)$", event_name, re.IGNORECASE)
        if not m:
            return input_data

        home = self._normalize_name(m.group(1).strip())
        away = self._normalize_name(m.group(2).strip())
        normalized = f"{home} vs {away}"

        if normalized.lower() != event_name.lower():
            input_data["event_name"] = normalized
            input_data["_event_normalized"] = True
            input_data.setdefault("_decisions", []).append(
                self.log_decision(
                    f"Normalized event: '{event_name}' → '{normalized}'",
                    "Team alias lookup",
                    confidence=0.90,
                )
            )

        return input_data
