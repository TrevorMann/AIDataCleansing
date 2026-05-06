"""Seeder: FSA→municipality from Wikipedia 'List of postal codes of Canada' pages."""

import re
import time
from seeders.base import Seeder


# One request per letter → full province table, not per-FSA lookup
_WIKI_API = "https://en.wikipedia.org/w/api.php"

# CA FSA first letter → ISO province code
_LETTER_TO_PROVINCE = {
    "A": "NL", "B": "NS", "C": "PE", "E": "NB",
    "G": "QC", "H": "QC", "J": "QC",
    "K": "ON", "L": "ON", "M": "ON", "N": "ON", "P": "ON",
    "R": "MB", "S": "SK", "T": "AB", "V": "BC",
    "X": "NT",  # X0A/X0B = NU; close enough for most use cases
    "Y": "YT",
}

_FSA_RE = re.compile(r'^[A-Z]\d[A-Z]$')


class WikipediaFSASeeder(Seeder):
    """
    Fetches Wikipedia 'List of postal codes of Canada: X' pages via the
    MediaWiki parse API (one request per letter), parses the HTML table,
    bulk-inserts into fsa_municipality_mapping. Idempotent.

    Config keys:
      letters            list of FSA first letters to fetch, e.g. [M, V, T]
                         defaults to all CA letters if omitted
      country            default "CA"
      rate_limit_seconds default 1.0 (be polite to Wikipedia)

    Manifest example:
      config:
        country: CA
        letters: [M, K, L, N, P]   # Ontario only
    """

    name = "wikipedia_fsa"
    domain = "real_estate"
    target_table = "fsa_municipality_mapping"
    source_tag = "wikipedia"
    schema_required = ["fsa_municipality_mapping"]

    _ALL_CA_LETTERS = ["A", "B", "C", "E", "G", "H", "J", "K", "L", "M",
                       "N", "P", "R", "S", "T", "V", "X", "Y"]

    def fetch(self) -> list:
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            raise RuntimeError("pip install requests beautifulsoup4")

        letters = self.config.get("letters") or self._ALL_CA_LETTERS
        rate_limit = float(self.config.get("rate_limit_seconds", 1.0))

        session = requests.Session()
        session.headers["User-Agent"] = (
            "FSASeeder/2.0 (data-cleaning project; "
            "contact: trevor.t.mann@gmail.com)"
        )

        results = []
        for letter in letters:
            page_title = f"List of postal codes of Canada: {letter}"
            try:
                resp = session.get(
                    _WIKI_API,
                    params={
                        "action": "parse",
                        "page": page_title,
                        "prop": "text",
                        "format": "json",
                        "disabletoc": True,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                if "error" in data:
                    print(f"  [{letter}] Wikipedia page not found: {page_title}")
                    continue

                html = data["parse"]["text"]["*"]
                rows = self._parse_table(html, letter)
                print(f"  [{letter}] {len(rows)} FSA rows from Wikipedia")
                results.extend(rows)
                time.sleep(rate_limit)

            except Exception as e:
                print(f"  [{letter}] fetch failed: {e}")

        return results

    def _parse_table(self, html: str, letter: str) -> list:
        """
        Wikipedia CA postal code pages use a grid of <td> cells, each containing:
          <b>M3A</b><br/>
          <span>North York<br/>(Parkwoods)</span>

        Strategy: find all <td> cells, extract <b> tag as FSA code, then take
        the span text up to the first '(' as municipality. Skip 'Not assigned'.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        rows = []

        for td in soup.find_all("td"):
            b_tag = td.find("b")
            if not b_tag:
                continue

            fsa = b_tag.get_text(strip=True).upper()
            if not _FSA_RE.match(fsa) or fsa[0] != letter:
                continue

            # Get remaining cell text after the FSA code
            cell_text = td.get_text(separator=" ", strip=True)
            # Remove the FSA code prefix
            remainder = cell_text[len(fsa):].strip()

            # Skip unassigned codes
            if not remainder or "not assigned" in remainder.lower():
                continue

            # Municipality is text before the first '(' (neighbourhood follows)
            municipality = remainder.split("(")[0].strip().rstrip(",").strip()
            if municipality and self._is_single_municipality(municipality):
                rows.append({"fsa": fsa, "municipality": municipality})

        return rows

    def _is_single_municipality(self, text: str) -> bool:
        """
        Rural FSAs (e.g. T0A, T0B) cover dozens of small towns. Wikipedia
        renders these as a sub-FSA lookup table packed into one cell:
          '0A0: Abee 0B0: Ardmore 0C0: Ashmont ...'
        These are LDUs (6-char codes), not 3-char FSA → single municipality
        mappings. Stats Can spatial join handles rural FSAs correctly via
        centroid-in-CSD. Skip them here — Wikipedia is urban gap-filler only.
        """
        return ":" not in text and len(text) <= 60

    def parse(self, payload: list) -> list:
        country = self.config.get("country", "CA")
        rows = []
        for item in payload:
            fsa = item["fsa"]
            province = _LETTER_TO_PROVINCE.get(fsa[0], "")
            if not province:
                continue
            rows.append({
                "fsa": fsa,
                "province": province,
                "country": country,
                "normalized_municipality": item["municipality"],
                "source": self.source_tag,
            })
        return rows

    def upsert(self, conn, rows: list) -> int:
        from db.upsert import bulk_insert_ignore
        import datetime
        today = datetime.date.today().isoformat()
        enriched = [{**r, "valid_from": today} for r in rows]
        return bulk_insert_ignore(
            conn, "fsa_municipality_mapping", enriched,
            ["fsa", "province", "country"],
        )
