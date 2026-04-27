NETHERLANDS_RULES = """
NETHERLANDS-SPECIFIC RULES:

POSTAL CODE:
- Format: XXXX XX (4 digits, space, 2 uppercase letters) — e.g. 1016 HW, 3521 AZ
- If space is missing (e.g. 1016HW), reformat to 1016 HW
- NEVER modify the numeric or letter portions — treat as authoritative
- Web search to VERIFY postal code matches address and city
- If missing: web search "[street address] [city] Netherlands postcode" to find it
- Only populate if search returns a single confident result; otherwise leave 'N/A'

PROVINCE:
- Use the official Dutch province name in English or Dutch — full name required:
  Noord-Holland, Zuid-Holland, Utrecht, Gelderland, Noord-Brabant, Overijssel,
  Limburg, Friesland, Groningen, Drenthe, Zeeland, Flevoland
- Do not use abbreviations

MUNICIPALITY:
- Use the official Dutch gemeente (municipality) name as it appears in real estate listings
- Web search "[postal code] gemeente Netherlands" or "[address] [city] Netherlands" to confirm
- Fill in for every record; use 'N/A' only if genuinely unresolvable

PHONE:
- European format: +31 XX XXXXXXX or +31 X XXXXXXXX
- Always include +31 country code — add it if missing
- Remove leading 0 from the area code when adding the country code (e.g. 020 → +31 20)
- If number cannot be formatted correctly, use 'N/A'

Country: Netherlands
"""
