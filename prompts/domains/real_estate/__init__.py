"""
Real estate domain prompts.

Sub-categories are country codes: CA, USA, NL, MX, JP.
Add a new country by creating a <cc>.py file with a RULES string, then adding it here.
"""

from .ca import RULES as _CA
from .usa import RULES as _USA
from .nl import RULES as _NL
from .mx import RULES as _MX
from .jp import RULES as _JP

DOMAIN_LABEL = "real estate listings — addresses, postal codes, neighbourhoods"

# Maps sub-category key → prompt string
_SUB_MAP: dict[str, str] = {
    "CA": _CA,
    "USA": _USA,
    "NL": _NL,
    "MX": _MX,
    "JP": _JP,
}


def get_prompt(sub: str | None = None) -> str:
    """Return the domain+sub prompt, or empty string if sub not recognized."""
    return _SUB_MAP.get(sub or "", "")


def list_subs() -> list[str]:
    return list(_SUB_MAP.keys())
