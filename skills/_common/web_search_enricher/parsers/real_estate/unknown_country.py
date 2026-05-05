"""Parser: resolve country code from web search."""

import re
from typing import Optional

_COUNTRY_PATTERNS = [
    (re.compile(r"\bCanada\b", re.IGNORECASE), "CA"),
    (re.compile(r"\bUnited States\b|\bUSA\b|\bU\.S\.A\b", re.IGNORECASE), "US"),
    (re.compile(r"\bUnited Kingdom\b|\bUK\b|\bGB\b", re.IGNORECASE), "GB"),
]


def parse(search_result: str, record: dict) -> Optional[dict]:
    if not search_result:
        return None

    for pattern, country_code in _COUNTRY_PATTERNS:
        if pattern.search(search_result):
            url_m = re.search(r"https?://\S+", search_result)
            return {
                "fields": {"country": country_code},
                "source_url": url_m.group(0) if url_m else None,
                "snippet": search_result[:200],
                "confidence": 0.65,
            }
    return None
