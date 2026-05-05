"""Parser: resolve phone country code from web search."""

import re
from typing import Optional


def parse(search_result: str, record: dict) -> Optional[dict]:
    if not search_result:
        return None
    # Look for E.164 country code mention (+1, +44, etc.)
    m = re.search(r"\+(\d{1,3})\s+(?:country|code|dial)", search_result, re.IGNORECASE)
    if not m:
        return None
    url_m = re.search(r"https?://\S+", search_result)
    return {
        "fields": {"_phone_country_code": f"+{m.group(1)}"},
        "source_url": url_m.group(0) if url_m else None,
        "snippet": search_result[:200],
        "confidence": 0.60,
    }
