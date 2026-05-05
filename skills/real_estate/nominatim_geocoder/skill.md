# Nominatim Geocoder Skill

## Purpose
Validate addresses via OpenStreetMap Nominatim geocoding. Catches invented or
misspelled addresses by round-tripping: forward geocode → get coordinates → verify
result municipality/postal code matches record.

## When to Use
- **DO**: When address validity is uncertain after spell correction + standardization
- **DO**: When municipality or postal code seems inconsistent with the address
- **DO**: As a secondary confidence signal (not sole source of truth)
- **DON'T**: For every record — rate limited to 1 req/sec, use only when needed
- **DON'T**: When address is already validated by a high-confidence prior signal

## Input
```python
{
  "address": str,           # Street address (standardized)
  "city": str,              # City name
  "postal_code": str,       # Postal code
  "state_province": str,    # Province code (e.g. "ON")
  "country": str,           # Country (e.g. "Canada")
}
```

## Output
```python
{
  "_geocode_lat": float | None,         # Latitude from Nominatim
  "_geocode_lon": float | None,         # Longitude from Nominatim
  "_geocode_display": str | None,       # Full display name from Nominatim
  "_geocode_confidence": float,         # 0.0-1.0 based on match quality
  "_geocode_validated": bool,           # True if result makes geographic sense
  "_decisions": [
    {
      "skill": "NominatimGeocoderSkill",
      "decision": "Geocoded: 25 Muir Avenue → (43.7234, -79.5432)",
      "reason": "Nominatim match: display='25 Muir Avenue, North York, Ontario'",
      "confidence": 0.90,
    }
  ]
}
```

## Rate Limit
- 1 request/second (Nominatim ToS)
- PG cache: `nominatim_cache(query_hash, response_json, fetched_at)` — TTL 30 days
- Cache hit = no HTTP request

## Confidence Scoring
- Result found + display name contains city/province: 0.90
- Result found + partial match: 0.70
- No result: 0.0
- Cached result: same score as live (cached at insert time)

## Configuration
```yaml
nominatim_geocoder:
  rate_limit: 1       # requests per second
  cache_ttl_days: 30  # cache lifetime
  countrycodes: "ca"  # restrict to Canada
```

## Dependencies
- address_standardizer (should run first to clean input)
- NominatimClient (cleaning/nominatim_client.py)

## Constraints
- OSM data quality varies — not authoritative for municipal boundary determination
- Nominatim ToS: max 1 req/sec, must use User-Agent
- No bulk geocoding
