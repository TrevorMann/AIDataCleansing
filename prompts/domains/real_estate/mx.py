SHORT_RULES = """
Mexico-specific rules:
- Postal format: 5-digit numeric (XXXXX). If missing, find via web search.
- Municipality: use the colonia (neighbourhood) name used in real estate listings (e.g. "Polanco", "Roma Norte")
"""

RULES = """
REAL ESTATE — MEXICO:

<postal_code>
- Format: 5-digit numeric (XXXXX) — e.g. 06500, 44100
- NEVER modify a full postal code — treat as authoritative
- Web search to VERIFY postal code matches address and city
- If missing: web search "[street address] [city] Mexico código postal"
- Only populate if search returns a single confident result; otherwise 'N/A'
</postal_code>

<state_province>
- Full Spanish name required (e.g. Ciudad de México, Jalisco, Nuevo León, Oaxaca)
- No abbreviations (not CDMX, not JAL)
- Valid: Aguascalientes, Baja California, Baja California Sur, Campeche, Chiapas, Chihuahua,
  Ciudad de México, Coahuila, Colima, Durango, Guanajuato, Guerrero, Hidalgo, Jalisco,
  México, Michoacán, Morelos, Nayarit, Nuevo León, Oaxaca, Puebla, Querétaro, Quintana Roo,
  San Luis Potosí, Sinaloa, Sonora, Tabasco, Tamaulipas, Tlaxcala, Veracruz, Yucatán, Zacatecas
</state_province>

<municipality>
Municipality is the colonia (neighbourhood) used in real estate listings — not the municipio.
Examples: "Polanco", "Roma Norte", "Condesa", "Narvarte", "Del Valle" (Mexico City);
"Providencia", "Chapalita" (Guadalajara).
- Web search "[postal code] colonia Mexico real estate" or "[address] [city] colonia"
- Fill in for every record; 'N/A' only if genuinely unresolvable
</municipality>

<phone>
- Format: +52 XX XXXX XXXX (Mexico City) or +52 XXX XXX XXXX (other areas)
- Add country code +52 if missing
- Remove leading 0 from area code when adding country code (055 → +52 55)
- If number cannot be formatted correctly, use 'N/A'
</phone>

<formatting>
Country: Mexico
</formatting>
"""
