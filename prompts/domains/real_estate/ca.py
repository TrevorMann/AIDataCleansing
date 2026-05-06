RULES = """
REAL ESTATE — CANADA:

STEP 0 — CROSS-PROVINCE SANITY CHECK (run before any web search):
Canadian postal codes encode province in the first letter. Check this BEFORE anything else:
  A = Newfoundland & Labrador | B = Nova Scotia | C = Prince Edward Island
  E = New Brunswick | G/H/J = Quebec | K/L/M/N/P = Ontario
  R = Manitoba | S = Saskatchewan | T = Alberta | V = British Columbia
  X = NWT/Nunavut | Y = Yukon
If the postal code's first letter does NOT match the record's province/city, flag immediately:
  "CROSS-PROVINCE MISMATCH: [postal code] belongs to [actual province], record says [stated province/city]. REQUIRES MANUAL REVIEW."
Do NOT attempt to web search or correct — just flag and move on.

POSTAL CODE CLASSIFICATION (determine type before any action):

  FULL postal code (6 characters, e.g. M6H 1E7, V6B 2W9):
  - NEVER modify or change. Treat as authoritative.
  - Web search to VERIFY the address/city matches the postal code.
  - If mismatch: flag as "POSTAL CODE MISMATCH: [code] does not match [address], [city]. KEEP ORIGINAL — requires review."
  - If confirmed: record confidence score in validation notes.

  PARTIAL postal code / FSA only (3 characters, e.g. M6H, V6B):
  - INCOMPLETE data — must be resolved.
  - Web search "[street address] [FSA] [city]" to find the full postal code.
  - CRITICAL: The same street name can exist in multiple FSA areas (e.g. Muir Avenue exists
    under M6H AND M9L — different streets in different neighbourhoods). Confirm the specific
    address before setting municipality or full code.
  - One confident match: complete the code, note "FSA [X] completed to [full code] via web search. Confidence: HIGH."
  - Ambiguous or multiple matches: set postal code to FSA + "?" (e.g. "M6H ?"),
    set municipality to 'N/A', flag "FSA AMBIGUOUS: multiple addresses found — requires manual review."
  - Never guess the full code without a confirmed web result.

  Missing postal code:
  - Web search "[street address] [city] [province] postal code".
  - Only populate if search returns a single confident result; otherwise 'N/A'.

MUNICIPALITY — REAL ESTATE NEIGHBOURHOOD:
Municipality MUST be the neighbourhood people search when looking for properties.
This differs from administrative boundaries (e.g. "The Annex" not "Toronto", "Little Italy" not "Dufferin",
"North York" not "City of Toronto", "Plateau-Mont-Royal" not "Montreal").

Process — follow in order:
1. Run cross-province check (Step 0) first. If flagged, set municipality to 'N/A' and skip remaining.
2. Determine postal code state (full / partial / missing) per rules above.
3. Full postal code: web search "[full postal code] real estate neighbourhood" to confirm municipality.
4. Partial FSA: web search "[address] [FSA] [city]" — resolve full postal code first, then municipality.
   Same address name in different FSA zones = different streets, different neighbourhoods.
5. Missing postal code: web search "[address] [city] [province]" — resolve postal code first.
6. Final cross-check: City + Address + Postal Code + Municipality must all align.
   Record confidence score (HIGH / MEDIUM / LOW) in validation notes.
   Flag any inconsistency even if unresolvable.

When unable to determine a value:
- Full postal code: keep original
- Partial/missing after failed search: 'N/A'
- Municipality: 'N/A' with explanation — should be rare

CRITICAL: NEVER change a full postal code. Update surrounding fields to align with it.

Postal format: A1A 1A1
Phone format: +1 (123) 456-7890
Province: full name (Ontario, British Columbia, Quebec, Alberta, etc.)
Country: Canada
"""
