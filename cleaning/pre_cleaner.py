"""
Deterministic pre-cleaning functions.
Handles everything that doesn't require web search:
  - Name / city title-casing
  - Country code → full name expansion
  - State/province abbreviation → full name expansion
  - Postal code format normalization (spacing, casing)
  - Phone number formatting (NA and EU)
No DB calls, no API calls, no side effects.
"""

import re

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

_COUNTRY_CODE_TO_NAME = {
    'CA': 'Canada', 'CAN': 'Canada',
    'USA': 'United States', 'US': 'United States',
    'NL': 'Netherlands', 'HOL': 'Netherlands',
    'MX': 'Mexico', 'MEX': 'Mexico',
    'JP': 'Japan', 'JPN': 'Japan',
}

_COUNTRY_NAME_TO_CODE = {
    'canada': 'CA',
    'united states': 'USA', 'united states of america': 'USA', 'america': 'USA',
    'netherlands': 'NL', 'holland': 'NL', 'the netherlands': 'NL',
    'mexico': 'MX', 'méxico': 'MX',
    'japan': 'JP',
}

_CA_PROVINCES = {
    'AB': 'Alberta', 'BC': 'British Columbia', 'MB': 'Manitoba',
    'NB': 'New Brunswick', 'NL': 'Newfoundland and Labrador',
    'NS': 'Nova Scotia', 'NT': 'Northwest Territories',
    'NU': 'Nunavut', 'ON': 'Ontario', 'PE': 'Prince Edward Island',
    'QC': 'Quebec', 'SK': 'Saskatchewan', 'YT': 'Yukon',
}

_US_STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
    'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
    'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho',
    'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas',
    'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
    'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
    'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
    'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
    'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
    'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
    'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia',
    'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia',
}

_NL_PROVINCES = {
    'NH': 'Noord-Holland', 'ZH': 'Zuid-Holland', 'UT': 'Utrecht',
    'GE': 'Gelderland', 'NB': 'Noord-Brabant', 'OV': 'Overijssel',
    'LI': 'Limburg', 'FR': 'Friesland', 'GR': 'Groningen',
    'DR': 'Drenthe', 'ZE': 'Zeeland', 'FL': 'Flevoland',
}

_MX_STATES = {
    'AGU': 'Aguascalientes', 'BCN': 'Baja California', 'BCS': 'Baja California Sur',
    'CAM': 'Campeche', 'CHP': 'Chiapas', 'CHH': 'Chihuahua',
    'CMX': 'Ciudad de México', 'CDMX': 'Ciudad de México',
    'COA': 'Coahuila', 'COL': 'Colima', 'DUR': 'Durango',
    'GTO': 'Guanajuato', 'GRO': 'Guerrero', 'HID': 'Hidalgo',
    'JAL': 'Jalisco', 'MEX': 'México', 'MIC': 'Michoacán',
    'MOR': 'Morelos', 'NAY': 'Nayarit', 'NLE': 'Nuevo León',
    'OAX': 'Oaxaca', 'PUE': 'Puebla', 'QUE': 'Querétaro',
    'ROO': 'Quintana Roo', 'SLP': 'San Luis Potosí', 'SIN': 'Sinaloa',
    'SON': 'Sonora', 'TAB': 'Tabasco', 'TAM': 'Tamaulipas',
    'TLA': 'Tlaxcala', 'VER': 'Veracruz', 'YUC': 'Yucatán', 'ZAC': 'Zacatecas',
}

_STREET_ABBREVS = {
    r'\bSt\.?\b': 'Street', r'\bAve\.?\b': 'Avenue', r'\bRd\.?\b': 'Road',
    r'\bBlvd\.?\b': 'Boulevard', r'\bDr\.?\b': 'Drive', r'\bLn\.?\b': 'Lane',
    r'\bCt\.?\b': 'Court', r'\bPl\.?\b': 'Place', r'\bCres\.?\b': 'Crescent',
    r'\bPkwy\.?\b': 'Parkway', r'\bHwy\.?\b': 'Highway',
}


# ---------------------------------------------------------------------------
# Individual cleaning functions
# ---------------------------------------------------------------------------

def get_country_code(country: str) -> str | None:
    """Return canonical code (CA, USA, NL, MX, JP) from either a code or full name."""
    if not country:
        return None
    upper = country.strip().upper()
    if upper in _COUNTRY_CODE_TO_NAME:
        return upper
    return _COUNTRY_NAME_TO_CODE.get(country.strip().lower())


def clean_name(name: str) -> str:
    if not name:
        return name
    return name.strip().title()


def clean_city(city: str) -> str:
    if not city:
        return city
    return city.strip().title()


def clean_address(address: str) -> str:
    if not address:
        return address
    result = address.strip()
    for pattern, replacement in _STREET_ABBREVS.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def expand_country(country: str) -> str:
    if not country:
        return country
    upper = country.strip().upper()
    return _COUNTRY_CODE_TO_NAME.get(upper, country)


def expand_state_province(state: str, country_code: str) -> str:
    if not state:
        return state
    upper = state.strip().upper()
    table = {
        'CA': _CA_PROVINCES,
        'USA': _US_STATES,
        'NL': _NL_PROVINCES,
        'MX': _MX_STATES,
    }.get(country_code, {})
    return table.get(upper, state)


def normalize_postal(postal: str, country_code: str) -> str:
    """Fix obvious formatting issues (missing space, wrong case) without web search."""
    if not postal:
        return postal
    p = postal.strip().upper()

    if country_code == 'CA':
        clean = p.replace(' ', '')
        if len(clean) == 6 and re.match(r'^[A-Z]\d[A-Z]\d[A-Z]\d$', clean):
            return f"{clean[:3]} {clean[3:]}"

    elif country_code == 'NL':
        clean = p.replace(' ', '')
        if len(clean) == 6 and re.match(r'^\d{4}[A-Z]{2}$', clean):
            return f"{clean[:4]} {clean[4:]}"

    elif country_code == 'JP':
        clean = p.replace('-', '').replace(' ', '')
        if len(clean) == 7 and clean.isdigit():
            return f"{clean[:3]}-{clean[3:]}"

    return postal


def format_phone(phone: str, country_code: str) -> str:
    """Format phone using regex. Returns formatted string or 'N/A' if unrecognizable."""
    if not phone or phone.strip() in ('N/A', '', 'n/a'):
        return phone

    if country_code in ('CA', 'USA', 'MX'):
        cleaned = re.sub(r'[\s\-\(\)\.+]', '', phone)
        if cleaned.startswith('1') and len(cleaned) == 11:
            cleaned = cleaned[1:]
        if len(cleaned) == 10 and cleaned.isdigit() and cleaned[0] not in ('0', '1'):
            return f"({cleaned[:3]}) {cleaned[3:6]}-{cleaned[6:]}"
        return 'N/A'

    elif country_code == 'NL':
        p = phone.strip()
        # Already has country code
        if re.match(r'^\+31', p):
            return p
        # Leading 0 → replace with +31
        digits = re.sub(r'[\s\-\(\)]', '', p)
        if digits.startswith('0') and len(digits) >= 9:
            return '+31 ' + digits[1:]
        return 'N/A'

    elif country_code == 'JP':
        p = phone.strip()
        if re.match(r'^\+81', p):
            return p
        digits = re.sub(r'[\s\-\(\)]', '', p)
        if digits.startswith('0') and len(digits) >= 10:
            return '+81 ' + digits[1:]
        return 'N/A'

    elif country_code == 'MX':
        cleaned = re.sub(r'[\s\-\(\)\.+]', '', phone)
        if cleaned.startswith('52') and len(cleaned) == 12:
            cleaned = cleaned[2:]
        if len(cleaned) == 10 and cleaned.isdigit():
            return f"+52 {cleaned[:2]} {cleaned[2:6]} {cleaned[6:]}"
        return 'N/A'

    return phone


# ---------------------------------------------------------------------------
# Batch-level helpers
# ---------------------------------------------------------------------------

def needs_research(record: dict) -> bool:
    """Return True if this record needs Claude (postal incomplete or municipality missing)."""
    municipality = (record.get('municipality') or '').strip()
    postal = (record.get('postal_code') or '').strip()

    municipality_missing = not municipality or municipality.upper() == 'N/A'
    # Strip formatting chars to count actual digits/letters
    postal_chars = re.sub(r'[\s\-]', '', postal)
    postal_incomplete = not postal_chars or len(postal_chars) < 5

    return municipality_missing or postal_incomplete


def pre_clean_record(record: dict) -> dict:
    """
    Apply all deterministic cleaning to a single record.
    Returns a new dict with cleaned values and a '_pre_clean_changes' list.
    Original record is not modified.
    """
    cleaned = dict(record)
    changes = []

    country_code = get_country_code(cleaned.get('country', ''))

    fields = [
        ('name',           clean_name),
        ('city',           clean_city),
        ('address',        clean_address),
    ]
    for field, fn in fields:
        if cleaned.get(field):
            new = fn(cleaned[field])
            if new != cleaned[field]:
                changes.append(f"{field}: {cleaned[field]!r} → {new!r}")
                cleaned[field] = new

    if cleaned.get('country'):
        new = expand_country(cleaned['country'])
        if new != cleaned['country']:
            changes.append(f"country: {cleaned['country']!r} → {new!r}")
            cleaned['country'] = new
        # Update country_code after expansion in case it changed
        country_code = get_country_code(cleaned['country']) or country_code

    if cleaned.get('state_province') and country_code:
        new = expand_state_province(cleaned['state_province'], country_code)
        if new != cleaned['state_province']:
            changes.append(f"state_province: {cleaned['state_province']!r} → {new!r}")
            cleaned['state_province'] = new

    if cleaned.get('phone') and country_code:
        new = format_phone(cleaned['phone'], country_code)
        if new != cleaned['phone']:
            changes.append(f"phone: {cleaned['phone']!r} → {new!r}")
            cleaned['phone'] = new

    if cleaned.get('postal_code') and country_code:
        new = normalize_postal(cleaned['postal_code'], country_code)
        if new != cleaned['postal_code']:
            changes.append(f"postal_code: {cleaned['postal_code']!r} → {new!r}")
            cleaned['postal_code'] = new

    cleaned['_pre_clean_changes'] = changes
    return cleaned
