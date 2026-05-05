"""Parser: resolve municipality ambiguity from web search results."""

import re
from typing import Optional

_PATTERN = re.compile(
    r"(?:municipality|city|town|village)\s+(?:of\s+)?([A-Z][a-zA-Z\s]+?)(?:\.|,|\s+is|\s+has)",
    re.IGNORECASE,
)


def parse(search_result: str, record: dict) -> Optional[dict]:
    if not search_result:
        return None

    m = _PATTERN.search(search_result)
    if not m:
        return None

    municipality = m.group(1).strip().title()
    if len(municipality) < 3 or len(municipality) > 50:
        return None

    url_m = re.search(r"https?://\S+", search_result)
    return {
        "fields": {"municipality": municipality},
        "source_url": url_m.group(0) if url_m else None,
        "snippet": search_result[:200],
        "confidence": 0.70,
    }
