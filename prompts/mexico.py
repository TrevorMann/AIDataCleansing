MEXICO_RULES = """
MEXICO-SPECIFIC RULES:

POSTAL CODE:
- Format: 5-digit numeric (XXXXX) — e.g. 06500, 44100
- NEVER modify a full postal code — treat as authoritative
- Web search to VERIFY postal code matches address and city
- If missing: web search "[street address] [city] Mexico código postal" to find it
- Only populate if search returns a single confident result; otherwise leave 'N/A'

STATE:
- Full Spanish name required (e.g. Ciudad de México, Jalisco, Nuevo León, Oaxaca)
- Do not use abbreviations (not CDMX, not JAL)
- Valid Mexican states: Aguascalientes, Baja California, Baja California Sur, Campeche,
  Chiapas, Chihuahua, Ciudad de México, Coahuila, Colima, Durango, Guanajuato, Guerrero,
  Hidalgo, Jalisco, México, Michoacán, Morelos, Nayarit, Nuevo León, Oaxaca, Puebla,
  Querétaro, Quintana Roo, San Luis Potosí, Sinaloa, Sonora, Tabasco, Tamaulipas,
  Tlaxcala, Veracruz, Yucatán, Zacatecas

MUNICIPALITY:
- Use the official municipio name or the urban neighbourhood (colonia) for real estate context
- Example: "Polanco" or "Roma Norte" for Mexico City real estate listings
- Web search "[postal code] colonia Mexico" or "[address] [city] colonia" to confirm
- Fill in for every record; use 'N/A' only if genuinely unresolvable

PHONE:
- Format: +52 XX XXXX XXXX (Mexico City) or +52 XXX XXX XXXX (other areas)
- Add country code +52 if missing
- Remove leading 0 from area code when adding country code (e.g. 055 → +52 55)
- If number cannot be formatted correctly, use 'N/A'

Country: Mexico
"""
