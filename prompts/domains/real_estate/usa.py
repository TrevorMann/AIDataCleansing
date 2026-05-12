SHORT_RULES = """
USA-specific rules:
- Postal (ZIP) format: XXXXX or XXXXX-XXXX. Never modify a full ZIP — only complete missing ones.
- Municipality: real estate neighbourhood (e.g. "Upper East Side" not "New York City")
"""

RULES = """
REAL ESTATE — USA:

<postal_code>
- Standard format: 5-digit (XXXXX) or ZIP+4 (XXXXX-XXXX)
- NEVER modify a full ZIP code — treat as authoritative
- Web search to VERIFY ZIP matches city and state
- If mismatch: flag as "ZIP MISMATCH: [zip] does not match [city], [state]. KEEP ORIGINAL — requires review."
- If ZIP is missing: web search "[street address] [city] [state] zip code"
- Only populate if search returns a single confident result; otherwise 'N/A'
</postal_code>

<state_province>
- Full state name required (e.g. New York, California, Texas) — no abbreviations in output
- Valid states: Alabama, Alaska, Arizona, Arkansas, California, Colorado, Connecticut, Delaware,
  Florida, Georgia, Hawaii, Idaho, Illinois, Indiana, Iowa, Kansas, Kentucky, Louisiana, Maine,
  Maryland, Massachusetts, Michigan, Minnesota, Mississippi, Missouri, Montana, Nebraska, Nevada,
  New Hampshire, New Jersey, New Mexico, New York, North Carolina, North Dakota, Ohio, Oklahoma,
  Oregon, Pennsylvania, Rhode Island, South Carolina, South Dakota, Tennessee, Texas, Utah,
  Vermont, Virginia, Washington, West Virginia, Wisconsin, Wyoming, District of Columbia
- Expand abbreviations (NY → New York). Flag if state doesn't match ZIP/city.
</state_province>

<municipality>
Municipality is the neighbourhood people search when looking for properties — not the administrative city.
Examples: "Upper East Side" not "New York City", "Beverly Hills" not "Los Angeles",
"Capitol Hill" not "Seattle", "French Quarter" not "New Orleans".
- Web search "[ZIP code] real estate neighbourhood" or "[address] [city] real estate neighbourhood"
- Fill in for every record; 'N/A' only if unresolvable after web search
</municipality>

<phone>
- North American format: (123) 456-7890
- Area code cannot start with 0 or 1
</phone>

<formatting>
Country: United States
</formatting>
"""
