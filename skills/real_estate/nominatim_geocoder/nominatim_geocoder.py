"""Nominatim geocoding skill — validate addresses via OpenStreetMap."""

import hashlib
import json
from typing import Any, Dict, Optional
from skills.base import BaseSkill

# Full country name → ISO 3166-1 alpha-2 (lowercase) for Nominatim countrycodes param
_COUNTRY_TO_ISO2: dict[str, str] = {
    "canada": "ca",
    "united states": "us",
    "usa": "us",
    "netherlands": "nl",
    "mexico": "mx",
    "japan": "jp",
}


class NominatimGeocoderSkill(BaseSkill):
    """Validate addresses via Nominatim reverse-geocoding. PG-cached."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = "real_estate"
        self.rate_limit = self.config.get("rate_limit", 1)
        self._countrycodes_fallback = self.config.get("countrycodes", "")
        self.cache_ttl_days = self.config.get("cache_ttl_days", 30)
        self.conn = self.config.get("pg_conn")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from cleaning.nominatim_client import NominatimClient
            self._client = NominatimClient(rate_limit_per_sec=self.rate_limit)
        return self._client

    def _resolve_countrycodes(self, country: str) -> str:
        """Derive ISO 3166-1 alpha-2 from the record's country field. Falls back to config."""
        iso = _COUNTRY_TO_ISO2.get(country.lower().strip(), "")
        return iso or self._countrycodes_fallback

    def _cache_key(self, street, city, postal, country, countrycodes) -> str:
        raw = f"{street}|{city}|{postal}|{country}|{countrycodes}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _cache_get(self, key: str) -> Optional[list]:
        if not self.conn:
            return None
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT response_json FROM nominatim_cache
                    WHERE query_hash = %s
                      AND fetched_at > NOW() - INTERVAL '%s days'
                    """,
                    (key, self.cache_ttl_days),
                )
                row = cur.fetchone()
                if row:
                    return json.loads(row[0])
        except Exception:
            pass
        return None

    def _cache_set(self, key: str, results: list):
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO nominatim_cache (query_hash, response_json)
                    VALUES (%s, %s)
                    ON CONFLICT (query_hash) DO UPDATE SET response_json = EXCLUDED.response_json, fetched_at = NOW()
                    """,
                    (key, json.dumps(results)),
                )
            self.conn.commit()
        except Exception:
            pass

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        street = input_data.get("address", "")
        city = input_data.get("city", "")
        postal = input_data.get("postal_code", "")
        country = input_data.get("country", "")
        countrycodes = self._resolve_countrycodes(country)

        if not street and not postal:
            return input_data

        cache_key = self._cache_key(street, city, postal, country, countrycodes)
        results = self._cache_get(cache_key)

        if results is None:
            try:
                client = self._get_client()
                results = client.search_structured(
                    street=street,
                    city=city,
                    postalcode=postal,
                    country=country,
                    countrycodes=countrycodes,
                ) or []
                self._cache_set(cache_key, results)
            except Exception as e:
                input_data["_geocode_confidence"] = 0.0
                input_data["_geocode_validated"] = False
                self.log_decision(
                    "Geocoding failed",
                    f"Nominatim error: {str(e)[:80]}",
                    confidence=0.0,
                )
                return input_data

        if not results:
            input_data["_geocode_confidence"] = 0.0
            input_data["_geocode_validated"] = False
            self.log_decision(
                "Address not found in Nominatim",
                f"No results for: {street}, {city}, {postal}",
                confidence=0.0,
            )
            return input_data

        best = results[0]
        lat = float(best.get("lat", 0))
        lon = float(best.get("lon", 0))
        display = best.get("display_name", "")

        # Confidence: high if city or province appears in display name
        city_lower = city.lower() if city else ""
        confidence = 0.90 if (city_lower and city_lower in display.lower()) else 0.70

        input_data["_geocode_lat"] = lat
        input_data["_geocode_lon"] = lon
        input_data["_geocode_display"] = display
        input_data["_geocode_confidence"] = confidence
        input_data["_geocode_validated"] = True

        self.log_decision(
            f"Geocoded: ({lat:.4f}, {lon:.4f})",
            f"Nominatim: {display[:80]}",
            confidence=confidence,
        )
        return input_data
