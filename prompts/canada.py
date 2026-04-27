CANADA_RULES = """
CANADA-SPECIFIC RULES:

STEP 0 — CROSS-PROVINCE SANITY CHECK (run before any web search):
Canadian postal codes encode province in the first letter. Check this BEFORE anything else:
  A = Newfoundland & Labrador | B = Nova Scotia | C = Prince Edward Island
  E = New Brunswick | G/H/J = Quebec | K/L/M/N/P = Ontario
  R = Manitoba | S = Saskatchewan | T = Alberta | V = British Columbia
  X = NWT/Nunavut | Y = Yukon
If the postal code's first letter does NOT match the record's province/city, flag immediately:
  "CROSS-PROVINCE MISMATCH: [postal code] belongs to [actual province], record says [stated province/city]. REQUIRES MANUAL REVIEW."
Do NOT attempt to web search or correct — just flag and move on. This catches obvious data entry errors
(e.g. V6B in a Toronto/Ontario record) without wasting a search.

POSTAL CODE CLASSIFICATION (determine type before any action):
There are three states — handle each differently:

  FULL postal code (6 characters, e.g. M6H 1E7, V6B 2W9):
  - NEVER modify or change. Treat as authoritative.
  - Web search to VERIFY the address/city Matches the postal code
  - If mismatch: flag as "POSTAL CODE MISMATCH: [code] does not match [address], [city]. KEEP ORIGINAL — requires review."
  - If confirmed: record confidence score in validation notes.

  PARTIAL postal code / FSA only (3 characters, e.g. M6H, V6B):
  - This is INCOMPLETE data, not a valid postal code. Must be resolved.
  - Web search "[street address] [FSA] [city]" to find the full postal code.
  - CRITICAL: The same street name can exist in multiple FSA areas (e.g. Muir Avenue exists
    under M6H AND M9L — these are different streets in different neighbourhoods). You MUST
    confirm which specific address matches before setting municipality or full code.
  - If web search returns exactly one confident match: complete the postal code and note
    "FSA [X] completed to [full code] via web search. Confidence: HIGH."
  - If ambiguous or multiple matches: set postal code to the FSA + "?" (e.g. "M6H ?"),
    set municipality to 'N/A', and flag "FSA AMBIGUOUS: multiple addresses found — requires manual review."
  - Never guess or assume the full code without a confirmed web result.

  Missing postal code:
  - Web search "[street address] [city] [province] postal code" to find it.
  - Only populate if search returns a single confident result.
  - If uncertain: leave as 'N/A' and note why.

MUNICIPALITY MAPPING (REAL ESTATE FOCUS):
Municipality MUST be filled in for EVERY record using the REAL ESTATE name — the neighbourhood
people actually search when looking for properties. This may differ from administrative boundaries
(e.g. "North York" not "Toronto", "Little Italy" not "Dufferin").

Process — follow in order:
1. Run cross-province check (Step 0) first. If flagged, set municipality to 'N/A' and skip remaining steps.
2. Determine postal code state (full / partial / missing) per rules above.
3. If full postal code: web search "[full postal code] real estate neighbourhood" to confirm municipality.
4. If partial FSA: web search "[address] [FSA] [city]" — resolve full postal code first, then derive municipality.
   - Remember: same address name in different FSA zones = different streets and different municipalities.
5. If missing postal code: web search "[address] [city] [province]" — resolve postal code first, then municipality.
6. Final cross-check: after cleaning, verify City + Address + Postal Code + Municipality all align.
   Record a confidence score (HIGH / MEDIUM / LOW) in validation notes.
   Flag any inconsistency even if you cannot resolve it.

When you cannot confidently determine a value:
- Postal Code: keep original (if full) or 'N/A' (if partial/missing after failed search)
- Municipality: 'N/A' with explanation — this should be rare
- City/Address: populate with best available information
- State/Province or Country: always use full names

CRITICAL: NEVER change a full postal code. Update surrounding fields to align with it, not the other way around.

Postal format: A1A 1A1
Phone format: +1 (123) 456-7890
Province: full name required (Ontario, British Columbia, Quebec, etc.)
Country: Canada
"""
