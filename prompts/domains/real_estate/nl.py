RULES = """
REAL ESTATE — NETHERLANDS:

POSTAL CODE:
- Format: XXXX XX (4 digits, space, 2 uppercase letters) — e.g. 1016 HW, 3521 AZ
- If space is missing (e.g. 1016HW), reformat to 1016 HW
- NEVER modify the numeric or letter portions — treat as authoritative
- Web search to VERIFY postal code matches address and city
- If missing: web search "[street address] [city] Netherlands postcode"
- Only populate if search returns a single confident result; otherwise 'N/A'

PROVINCE:
- Use the official Dutch province name — full name required:
  Noord-Holland, Zuid-Holland, Utrecht, Gelderland, Noord-Brabant, Overijssel,
  Limburg, Friesland, Groningen, Drenthe, Zeeland, Flevoland
- No abbreviations

MUNICIPALITY — REAL ESTATE NEIGHBOURHOOD:
Municipality is the official Dutch gemeente name as used in real estate listings.
Examples: "Amsterdam-Centrum", "Jordaan", "De Pijp", "Rotterdam-Centrum", "Kralingen".
- Web search "[postal code] gemeente Netherlands real estate" to confirm
- Fill in for every record; 'N/A' only if genuinely unresolvable

PHONE:
- European format: +31 XX XXXXXXX or +31 X XXXXXXXX
- Always include +31 country code
- Remove leading 0 from area code when adding country code (020 → +31 20)
- If number cannot be formatted correctly, use 'N/A'

Country: Netherlands
"""
