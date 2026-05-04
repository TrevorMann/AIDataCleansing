import json
import urllib.request
import urllib.parse
import time
from typing import Optional, List, Dict


class NominatimClient:
    """Rate-limited Nominatim API client."""

    BASE_URL = "https://nominatim.openstreetmap.org/search"
    USER_AGENT = "ai-data-cleaning/1.0"

    def __init__(self, rate_limit_per_sec: int = 1):
        """Initialize with rate limit (requests per second)."""
        self.rate_limit_per_sec = rate_limit_per_sec
        self.min_request_interval = 1.0 / rate_limit_per_sec
        self.last_request_time = 0

    def _enforce_rate_limit(self):
        """Sleep to enforce rate limit."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()

    def search_structured(
        self,
        street: Optional[str] = None,
        city: Optional[str] = None,
        postalcode: Optional[str] = None,
        country: Optional[str] = None,
        countrycodes: str = "ca",
        limit: int = 5,
    ) -> Optional[List[Dict]]:
        """
        Structured search (street + city + postal code).
        Returns list of results or None on error.
        """
        try:
            params = {
                "format": "jsonv2",
                "addressdetails": 1,
                "countrycodes": countrycodes,
                "limit": limit,
            }

            if street:
                params["street"] = street
            if city:
                params["city"] = city
            if postalcode:
                params["postalcode"] = postalcode
            if country:
                params["country"] = country

            url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"

            # Enforce rate limit
            self._enforce_rate_limit()

            req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data if data else None

        except Exception as e:
            # Log error, return None
            print(f"Nominatim API error: {e}")
            return None

    def search_freeform(self, query: str, countrycodes: str = "ca", limit: int = 5) -> Optional[List[Dict]]:
        """Free-form search query."""
        try:
            params = {
                "q": query,
                "format": "jsonv2",
                "addressdetails": 1,
                "countrycodes": countrycodes,
                "limit": limit,
            }

            url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"

            # Enforce rate limit
            self._enforce_rate_limit()

            req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data if data else None

        except Exception as e:
            print(f"Nominatim API error: {e}")
            return None
