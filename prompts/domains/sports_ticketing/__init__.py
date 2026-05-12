"""
Sports ticketing domain prompts — NOT YET IMPLEMENTED.

Sub-categories will be ticketing platforms (ticketmaster, axs, seatgeek, general).
Add by creating <platform>.py with RULES and SHORT_RULES strings, then registering below.
"""

DOMAIN_LABEL = "sports and entertainment ticketing records"

_SUB_MAP: dict[str, str] = {
    # "ticketmaster": _TICKETMASTER,
    # "axs": _AXS,
    # "general": _GENERAL,
}


def get_prompt(sub: str | None = None) -> str:
    """Returns empty string — domain not yet implemented. Pipeline falls back to general rules only."""
    return _SUB_MAP.get(sub or "", "")


def list_subs() -> list[str]:
    return list(_SUB_MAP.keys())
