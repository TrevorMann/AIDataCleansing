USA_RULES = """
USA-SPECIFIC RULES:

POSTAL CODE (ZIP CODE):
- Standard format: 5-digit (XXXXX) or ZIP+4 (XXXXX-XXXX)
- NEVER modify a full ZIP code — treat as authoritative
- Web search to VERIFY ZIP matches city and state
- If mismatch: flag as "ZIP MISMATCH: [zip] does not match [city], [state]. KEEP ORIGINAL — requires review."
- If ZIP is missing: web search "[street address] [city] [state] zip code" to find it
- Only populate if search returns a single confident result; otherwise leave 'N/A'

STATE:
- Full state name required (e.g. New York, California, Texas) — abbreviations are not acceptable in output
- Valid states: Alabama, Alaska, Arizona, Arkansas, California, Colorado, Connecticut, Delaware,
  Florida, Georgia, Hawaii, Idaho, Illinois, Indiana, Iowa, Kansas, Kentucky, Louisiana, Maine,
  Maryland, Massachusetts, Michigan, Minnesota, Mississippi, Missouri, Montana, Nebraska, Nevada,
  New Hampshire, New Jersey, New Mexico, New York, North Carolina, North Dakota, Ohio, Oklahoma,
  Oregon, Pennsylvania, Rhode Island, South Carolina, South Dakota, Tennessee, Texas, Utah,
  Vermont, Virginia, Washington, West Virginia, Wisconsin, Wyoming, District of Columbia
- If state abbreviation is present (e.g. NY), expand to full name (New York)
- If state does not match ZIP/city: flag as "STATE MISMATCH — requires review"

MUNICIPALITY:
- Use the real estate neighbourhood name people search when buying property
- Examples: "Upper East Side" not just "New York"; "Beverly Hills" not "Los Angeles"
- Web search "[ZIP code] real estate neighbourhood" or "[address] [city] neighbourhood" to confirm
- Fill in for every record; use 'N/A' only if genuinely unresolvable after web search

PHONE:
- North American format: (123) 456-7890
- Area code cannot start with 0 or 1

Country: United States
"""
