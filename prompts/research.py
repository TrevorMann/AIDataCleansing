"""
Focused research prompts for the postal code + municipality lookup step.
Names, phones, countries, and states are already cleaned before Claude sees the data.
Claude's only job here is web research.
"""

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

_CANADA_RESEARCH_NOTES = """
Canada-specific rules:
- Postal format: A1A 1A1 (include space). FSA only (3 chars) must be completed via web search.
- Cross-province check: first letter encodes province (V=BC, M/K/L/N/P=ON, H/J/G=QC, T=AB, etc.)
  If the postal first letter doesn't match the province, flag as CROSS-PROVINCE MISMATCH.
- Municipality: real estate neighbourhood (e.g. "The Annex" not "Toronto", "Plateau" not "Montreal")
"""

_USA_RESEARCH_NOTES = """
USA-specific rules:
- Postal (ZIP) format: XXXXX or XXXXX-XXXX. Never modify a full ZIP — only complete missing ones.
- Municipality: real estate neighbourhood (e.g. "Upper East Side" not "New York City")
"""

_NL_RESEARCH_NOTES = """
Netherlands-specific rules:
- Postal format: XXXX XX (4 digits, space, 2 letters). If incomplete, find via web search.
- Municipality: use the official gemeente name (e.g. "Amsterdam", "Rotterdam-Centrum")
"""

_MX_RESEARCH_NOTES = """
Mexico-specific rules:
- Postal format: 5-digit numeric (XXXXX). If missing, find via web search.
- Municipality: use the colonia (neighbourhood) name used in real estate listings (e.g. "Polanco", "Roma Norte")
"""

_JP_RESEARCH_NOTES = """
Japan-specific rules:
- Postal format: XXX-XXXX. If missing, find via web search (results may be less reliable — flag as LOW confidence if uncertain).
- Municipality: use the ward (ku) or district name (e.g. "Shinjuku-ku", "Namba")
"""

_COUNTRY_NOTES = {
    'CA': _CANADA_RESEARCH_NOTES,
    'USA': _USA_RESEARCH_NOTES,
    'NL': _NL_RESEARCH_NOTES,
    'MX': _MX_RESEARCH_NOTES,
    'JP': _JP_RESEARCH_NOTES,
}


def build_research_prompt(country_scope: str, data: str) -> str:
    """Build the focused research prompt for a given country and formatted data table."""
    notes = _COUNTRY_NOTES.get(country_scope, "")
    return _BASE_RESEARCH + notes + "\n" + data
