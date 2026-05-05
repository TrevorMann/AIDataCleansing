"""Parser: resolve organization from email domain via web search."""

import re
from typing import Optional


def parse(search_result: str, record: dict) -> Optional[dict]:
    if not search_result:
        return None
    # Look for "X is a company/organization" pattern
    m = re.search(r"([A-Z][a-zA-Z\s]+?)\s+is\s+(?:a|an)\s+(?:company|organization|corporation)", search_result, re.IGNORECASE)
    if not m:
        return None
    url_m = re.search(r"https?://\S+", search_result)
    return {
        "fields": {"_email_org": m.group(1).strip()},
        "source_url": url_m.group(0) if url_m else None,
        "snippet": search_result[:200],
        "confidence": 0.60,
    }
