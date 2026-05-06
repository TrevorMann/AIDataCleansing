RULES = """
REAL ESTATE — JAPAN:

POSTAL CODE:
- Format: XXX-XXXX (3 digits, hyphen, 4 digits) — e.g. 104-0061, 163-8001
- NEVER modify a full postal code — treat as authoritative
- Web search to VERIFY postal code matches address and city
- If missing: web search "[street address] [city] Japan postal code"
- Only populate if search returns a single confident result; otherwise 'N/A'
- Note: web search for Japanese addresses may be less reliable — flag as LOW confidence if uncertain

PREFECTURE (State/Province):
- Full English name required — e.g. Tokyo, Osaka, Kyoto, Kanagawa, Aichi
- No abbreviations or Japanese characters in output
- Common: Tokyo, Osaka, Kanagawa, Aichi, Saitama, Chiba, Hyogo, Fukuoka,
  Hokkaido, Shizuoka, Ibaraki, Hiroshima, Kyoto, Miyagi, Niigata, Nagano, Tochigi,
  Gunma, Okayama, Fukushima, Gifu, Mie, Kumamoto, Kagoshima, Okinawa

MUNICIPALITY — REAL ESTATE WARD/DISTRICT:
Municipality is the ward (ku) or district name used in real estate listings.
Examples (Tokyo): Chuo-ku, Shinjuku-ku, Minato-ku, Shibuya-ku, Sumida-ku.
Examples (Osaka): Namba, Shinsaibashi, Umeda, Tennoji.
- Web search "[postal code] Japan ward real estate" or "[address] [city] Japan neighbourhood"
- Note: Japanese address searches may have lower confidence — flag LOW if uncertain
- Fill in for every record; 'N/A' if genuinely unresolvable

PHONE:
- Format: +81 XX XXXX XXXX (landline) or +81 XX XXXX XXXX (mobile)
- Add country code +81 if missing
- Replace leading 0 with +81 (03-1234-5678 → +81 3-1234-5678)
- If number cannot be formatted correctly, use 'N/A'

Country: Japan
"""
