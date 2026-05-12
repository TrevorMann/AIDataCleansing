"""
Focused research prompt for the postal code + municipality lookup phase.
Names, phones, countries, and states are already cleaned before Claude sees the data.
Claude's only job here is web research.

Country-specific SHORT_RULES are imported from each sub-category file — single source of truth.
"""

from prompts.domains.real_estate.ca import SHORT_RULES as _CA
from prompts.domains.real_estate.usa import SHORT_RULES as _USA
from prompts.domains.real_estate.nl import SHORT_RULES as _NL
from prompts.domains.real_estate.mx import SHORT_RULES as _MX
from prompts.domains.real_estate.jp import SHORT_RULES as _JP

_BASE_RESEARCH = """You are a data researcher. For each record below, do TWO things only:
1. Verify or complete the postal code (if missing or incomplete)
2. Find the real estate neighbourhood name for the municipality field

Names, phones, and addresses are already standardized — do not change them.

EXECUTION ORDER — follow exactly:
PHASE 1: Fire ALL web_search calls you need in one batch before writing any output.
PHASE 2: Write the output table using your search results.

Return ONLY this table, no preamble or explanation:
| ID | Postal Code | Municipality | Validation Notes |

Rules:
- If you cannot confidently determine a value, use N/A and note why
- Postal Code: return the verified/completed value, or N/A if unresolvable
- Municipality: real estate neighbourhood (not administrative boundary)
- Validation Notes: confidence level (HIGH/MEDIUM/LOW) and what you searched

DATA TO RESEARCH:
"""

_COUNTRY_NOTES: dict[str, str] = {
    "CA": _CA,
    "USA": _USA,
    "NL": _NL,
    "MX": _MX,
    "JP": _JP,
}


def build_research_prompt(country_scope: str, data: str) -> str:
    """Build the focused research prompt for a given country and formatted data table."""
    notes = _COUNTRY_NOTES.get(country_scope, "")
    return _BASE_RESEARCH + notes + "\n" + data
