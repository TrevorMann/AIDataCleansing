JAPAN_RULES = """
JAPAN-SPECIFIC RULES:

POSTAL CODE:
- Format: XXX-XXXX (3 digits, hyphen, 4 digits) — e.g. 104-0061, 163-8001
- NEVER modify a full postal code — treat as authoritative
- Web search to VERIFY postal code matches address and city
- If missing: web search "[street address] [city] Japan postal code" to find it
- Only populate if search returns a single confident result; otherwise leave 'N/A'

PREFECTURE (State/Province):
- Use the English prefecture name in full — e.g. Tokyo, Osaka, Kyoto, Kanagawa, Aichi
- Do not use abbreviations or Japanese characters in output
- Common prefectures: Tokyo, Osaka, Kanagawa, Aichi, Saitama, Chiba, Hyogo, Fukuoka,
  Hokkaido, Shizuoka, Ibaraki, Hiroshima, Kyoto, Miyagi, Niigata, Nagano, Tochigi,
  Gunma, Okayama, Fukushima, Gifu, Mie, Kumamoto, Kagoshima, Okinawa

MUNICIPALITY:
- Use the ward (ku), city, or district name for real estate context
- Examples: Chuo-ku, Shinjuku-ku, Minato-ku (Tokyo); Namba (Osaka)
- Web search "[postal code] Japan ward neighbourhood" or "[address] [city] Japan neighbourhood" to confirm
- Note: web search for Japanese addresses may be unreliable — flag as LOW confidence if uncertain
- Fill in for every record; use 'N/A' if genuinely unresolvable

PHONE:
- Format: +81 XX XXXX XXXX (landline) or +81 XX XXXX XXXX (mobile)
- Add country code +81 if missing
- Replace leading 0 with +81 (e.g. 03-1234-5678 → +81 3-1234-5678)
- If number cannot be formatted correctly, use 'N/A'

Country: Japan
"""
