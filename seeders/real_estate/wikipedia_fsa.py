"""Seeder: FSA→municipality data from Wikipedia redirect pages."""

import re
import time
from typing import Any
from seeders.base import Seeder


class WikipediaFSASeeder(Seeder):
    """Seed municipality_lookup_cache from Wikipedia FSA article redirects.

    Fetches the Wikipedia page for each FSA prefix (e.g. "M9L postal code")
    and extracts the municipality name from the title or first paragraph.
    Idempotent — INSERT ON CONFLICT DO NOTHING.
    """

    name = "wikipedia_fsa"
    domain = "real_estate"
    target_table = "municipality_lookup_cache"
    source_tag = "wikipedia"
    schema_required = ["municipality_lookup_cache"]

    # Ontario FSA prefixes — extend for other provinces
    _FSA_PREFIXES = [
        "M1A", "M1B", "M1C", "M1E", "M1G", "M1H", "M1J", "M1K", "M1L",
        "M1M", "M1N", "M1P", "M1R", "M1S", "M1T", "M1V", "M1W", "M1X",
        "M2H", "M2J", "M2K", "M2L", "M2M", "M2N", "M2P", "M2R",
        "M3A", "M3B", "M3C", "M3H", "M3J", "M3K", "M3L", "M3M", "M3N",
        "M4A", "M4B", "M4C", "M4E", "M4G", "M4H", "M4J", "M4K", "M4L",
        "M4M", "M4N", "M4P", "M4R", "M4S", "M4T", "M4V", "M4W", "M4X", "M4Y",
        "M5A", "M5B", "M5C", "M5E", "M5G", "M5H", "M5J", "M5K", "M5L",
        "M5M", "M5N", "M5P", "M5R", "M5S", "M5T", "M5V", "M5W", "M5X",
        "M6A", "M6B", "M6C", "M6E", "M6G", "M6H", "M6J", "M6K", "M6L",
        "M6M", "M6N", "M6P", "M6R", "M6S",
        "M7A", "M7R", "M7Y",
        "M8V", "M8W", "M8X", "M8Y", "M8Z",
        "M9A", "M9B", "M9C", "M9L", "M9M", "M9N", "M9P", "M9R", "M9V", "M9W",
    ]

    def fetch(self) -> list:
        """Fetch Wikipedia data for each FSA prefix. Returns list of (fsa, municipality) pairs."""
        try:
            import requests
        except ImportError:
            raise RuntimeError("requests library required: pip install requests")

        results = []
        rate_limit = self.config.get("rate_limit_seconds", 0.5)
        session = requests.Session()
        session.headers["User-Agent"] = "FSASeeder/1.0 (data-cleaning project; contact: trevor.t.mann@gmail.com)"

        for fsa in self._FSA_PREFIXES:
            try:
                resp = session.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "titles": f"{fsa} postal code",
                        "prop": "extracts",
                        "exintro": True,
                        "explaintext": True,
                        "redirects": True,
                        "format": "json",
                    },
                    timeout=10,
                )
                data = resp.json()
                pages = data.get("query", {}).get("pages", {})
                for page in pages.values():
                    if "missing" in page:
                        continue
                    extract = page.get("extract", "")
                    municipality = self._extract_municipality(fsa, extract)
                    if municipality:
                        results.append({"fsa": fsa, "municipality": municipality})
                time.sleep(rate_limit)
            except Exception as e:
                print(f"  Wikipedia FSA {fsa}: {e}")
        return results

    def _extract_municipality(self, fsa: str, extract: str) -> str:
        """Best-effort extract municipality from Wikipedia FSA page intro."""
        if not extract:
            return ""
        first_line = extract.split("\n")[0]
        # Look for "located in X" or "neighborhood in X" or "area of X"
        for pattern in [
            r"(?:located in|neighbourhood in|area of|part of|district of)\s+([A-Z][a-zA-Z\s]+?)(?:\.|,|$)",
            r"([A-Z][a-zA-Z\s]+?)\s+is a postal",
        ]:
            m = re.search(pattern, first_line)
            if m:
                return m.group(1).strip()
        return ""

    def parse(self, payload: list) -> list:
        rows = []
        for item in payload:
            rows.append({
                "fsa": item["fsa"],
                "province": "ON",
                "municipality": item["municipality"],
                "source": self.source_tag,
                "confidence": 0.80,
            })
        return rows

    def upsert(self, conn, rows: list) -> int:
        if not rows:
            return 0
        params = [(r["fsa"], r["province"], r["municipality"], r["source"], r["confidence"]) for r in rows]
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO municipality_lookup_cache (fsa, province, municipality, source, confidence_score)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (fsa, province) DO NOTHING
                """,
                params,
            )
        conn.commit()
        return len(rows)
