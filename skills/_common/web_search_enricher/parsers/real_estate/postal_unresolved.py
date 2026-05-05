"""Parser: resolve municipality from web search results for postal_unresolved gap."""

import re
from typing import Optional

_KNOWN_MUNICIPALITIES = [
    "Toronto", "Scarborough", "North York", "Etobicoke", "York",
    "East York", "Mississauga", "Brampton", "Vaughan", "Markham",
    "Richmond Hill", "Oakville", "Burlington", "Hamilton", "Ajax",
    "Pickering", "Whitby", "Oshawa", "Barrie", "Kingston",
    "Ottawa", "London", "Windsor", "Sudbury", "Thunder Bay",
]

_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _KNOWN_MUNICIPALITIES) + r")\b",
    re.IGNORECASE,
)


def parse(search_result: str, record: dict) -> Optional[dict]:
    """Extract municipality from Tavily search snippet.

    Returns dict with fields, source_url, snippet, confidence — or None.
    """
    if not search_result:
        return None

    m = _PATTERN.search(search_result)
    if not m:
        return None

    municipality = m.group(1).title()

    url_m = re.search(r"https?://\S+", search_result)
    return {
        "fields": {"municipality": municipality},
        "source_url": url_m.group(0) if url_m else None,
        "snippet": search_result[:200],
        "confidence": 0.75,
    }
