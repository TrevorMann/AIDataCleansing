"""Guardrail validation functions for CRUD tool operations.

All functions raise GuardrailError on violation, return None on pass.
No DB calls or side effects — pure validation only.

Country validation is intentionally broad: any recognizable country name or
code is accepted. Unknown values trigger a GuardrailError with a suggestion.
Use normalize_country() before inserting to standardize to ISO codes.

Misspelling approach: normalize_country() uses difflib.get_close_matches()
against a comprehensive alias table. For production pipelines the spell_checker
skill (symspellpy) provides higher-quality corrections loaded from the DB;
these guardrails are a lightweight safety net, not a cleaning tool.
"""
import difflib


class GuardrailError(Exception):
    pass


# ── Country normalization ─────────────────────────────────────────────────────
# Maps lowercase country name / code variants → ISO 3166-1 alpha-2 code.
# Add rows here to support new country aliases; no code changes needed elsewhere.
_COUNTRY_ALIASES: dict[str, str] = {
    "ca": "CA", "canada": "CA", "cdn": "CA", "canadian": "CA",
    "us": "US", "usa": "US", "united states": "US",
    "united states of america": "US", "america": "US", "american": "US",
    "u.s.": "US", "u.s.a.": "US",
    "mx": "MX", "mexico": "MX", "mexican": "MX", "méxico": "MX",
    "jp": "JP", "japan": "JP", "japanese": "JP", "nippon": "JP",
    "nl": "NL", "netherlands": "NL", "dutch": "NL", "holland": "NL",
    "the netherlands": "NL",
    "gb": "GB", "uk": "GB", "united kingdom": "GB", "britain": "GB",
    "great britain": "GB", "england": "GB",
    "de": "DE", "germany": "DE", "german": "DE", "deutschland": "DE",
    "fr": "FR", "france": "FR", "french": "FR",
    "au": "AU", "australia": "AU", "australian": "AU",
    "br": "BR", "brazil": "BR", "brasil": "BR", "brazilian": "BR",
    "in": "IN", "india": "IN", "indian": "IN",
    "cn": "CN", "china": "CN", "chinese": "CN",
    "es": "ES", "spain": "ES", "spanish": "ES", "españa": "ES",
    "it": "IT", "italy": "IT", "italian": "IT",
    "se": "SE", "sweden": "SE", "swedish": "SE",
    "no": "NO", "norway": "NO", "norwegian": "NO",
    "dk": "DK", "denmark": "DK", "danish": "DK",
    "fi": "FI", "finland": "FI", "finnish": "FI",
    "be": "BE", "belgium": "BE", "belgian": "BE",
    "ch": "CH", "switzerland": "CH", "swiss": "CH",
    "at": "AT", "austria": "AT", "austrian": "AT",
    "pt": "PT", "portugal": "PT", "portuguese": "PT",
    "pl": "PL", "poland": "PL", "polish": "PL",
    "ru": "RU", "russia": "RU", "russian": "RU",
    "kr": "KR", "south korea": "KR", "korea": "KR", "korean": "KR",
    "sg": "SG", "singapore": "SG",
    "hk": "HK", "hong kong": "HK",
    "nz": "NZ", "new zealand": "NZ",
    "za": "ZA", "south africa": "ZA",
    "ae": "AE", "uae": "AE", "united arab emirates": "AE",
    "ng": "NG", "nigeria": "NG", "nigerian": "NG",
    "eg": "EG", "egypt": "EG", "egyptian": "EG",
    "ke": "KE", "kenya": "KE", "kenyan": "KE",
    "ar": "AR", "argentina": "AR", "argentinian": "AR",
    "co": "CO", "colombia": "CO", "colombian": "CO",
    "cl": "CL", "chile": "CL", "chilean": "CL",
    "pe": "PE", "peru": "PE", "peruvian": "PE",
    "ve": "VE", "venezuela": "VE", "venezuelan": "VE",
    "ph": "PH", "philippines": "PH", "philippine": "PH", "filipino": "PH",
    "id": "ID", "indonesia": "ID", "indonesian": "ID",
    "my": "MY", "malaysia": "MY", "malaysian": "MY",
    "th": "TH", "thailand": "TH", "thai": "TH",
    "vn": "VN", "vietnam": "VN", "vietnamese": "VN",
    "pk": "PK", "pakistan": "PK", "pakistani": "PK",
    "bd": "BD", "bangladesh": "BD", "bangladeshi": "BD",
    "ie": "IE", "ireland": "IE", "irish": "IE",
    "gr": "GR", "greece": "GR", "greek": "GR",
    "cz": "CZ", "czech republic": "CZ", "czech": "CZ", "czechia": "CZ",
    "ro": "RO", "romania": "RO", "romanian": "RO",
    "hu": "HU", "hungary": "HU", "hungarian": "HU",
    "il": "IL", "israel": "IL", "israeli": "IL",
    "sa": "SA", "saudi arabia": "SA", "saudi": "SA",
    "tr": "TR", "turkey": "TR", "turkish": "TR", "türkiye": "TR",
    "tw": "TW", "taiwan": "TW", "taiwanese": "TW",
}


def normalize_country(value: str) -> tuple[str | None, str | None]:
    """Return (normalized_iso_code, suggestion) for a country string.

    Returns:
        (code, code)   — exact alias match
        (None, code)   — fuzzy match; suggestion is what we think they meant
        (None, None)   — unrecognizable; no suggestion

    Fuzzy matching uses difflib with cutoff=0.80 so "Canads" → CA but
    "XYZ123" → None. For higher-quality corrections on large batches, prefer
    the spell_checker skill which uses symspellpy + domain-seeded corrections.
    """
    if not value:
        return (None, None)
    key = value.strip().lower()
    if key in _COUNTRY_ALIASES:
        code = _COUNTRY_ALIASES[key]
        return (code, code)
    matches = difflib.get_close_matches(key, _COUNTRY_ALIASES.keys(), n=1, cutoff=0.80)
    if matches:
        return (None, _COUNTRY_ALIASES[matches[0]])
    return (None, None)


def check_country(country: str) -> None:
    """Raise GuardrailError only if country is set but completely unrecognizable.

    Accepts any value that normalize_country() can resolve exactly or via fuzzy
    match. This allows full names ("Canada"), common aliases ("America"), and
    ISO codes ("CA") without restricting to a hard-coded domain list.
    """
    if not country:
        return
    normalized, suggestion = normalize_country(country)
    if normalized is None and suggestion is None:
        raise GuardrailError(
            f"Country '{country}' is not recognizable. "
            f"Use a standard ISO code (e.g., CA, US, MX, GB) or full name."
        )


# ── US state validation ───────────────────────────────────────────────────────
# General: US state names are public knowledge, not domain-specific data.

PROTECTED_RAW_FIELDS = {'id', 'raw_data_id', 'imported_at'}
PROTECTED_CLEANED_FIELDS = {'id', 'raw_data_id', 'cleaned_at'}

US_STATE_ABBREVIATIONS = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC',
}

US_STATE_FULL_NAMES = {
    'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
    'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho',
    'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana',
    'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota',
    'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada',
    'New Hampshire', 'New Jersey', 'New Mexico', 'New York',
    'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma', 'Oregon',
    'Pennsylvania', 'Rhode Island', 'South Carolina', 'South Dakota',
    'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington',
    'West Virginia', 'Wisconsin', 'Wyoming', 'District of Columbia',
}


def check_age(age) -> None:
    if age is None:
        return
    if not isinstance(age, int) or age < 1 or age > 120:
        raise GuardrailError(f"Age must be an integer between 1 and 120, got: {age!r}")


def check_protected_fields(fields: dict, table: str) -> None:
    protected = PROTECTED_RAW_FIELDS if table == 'raw_data' else PROTECTED_CLEANED_FIELDS
    bad = set(fields.keys()) & protected
    if bad:
        raise GuardrailError(f"Cannot update protected fields: {', '.join(sorted(bad))}")


def check_no_wildcard_update(fields: dict) -> None:
    if not fields:
        raise GuardrailError("Update requires at least one field to change.")


def check_delete_confirmation(confirm: str) -> None:
    if confirm != 'yes':
        raise GuardrailError("Delete requires confirm='yes' exactly.")


def check_delete_not_bulk(record_id) -> None:
    if not isinstance(record_id, int) or record_id <= 0:
        raise GuardrailError(
            f"Delete requires a specific positive integer ID, got: {record_id!r}"
        )


def check_usa_state(state_province: str) -> None:
    if not state_province:
        return
    if state_province.upper() in US_STATE_ABBREVIATIONS:
        raise GuardrailError(
            f"State '{state_province}' is an abbreviation. Use the full state name "
            f"(e.g. 'New York', 'California')."
        )
    if state_province not in US_STATE_FULL_NAMES:
        raise GuardrailError(
            f"Invalid US state: '{state_province}'. Must be a valid US state full name."
        )


def check_nl_phone_format(phone: str) -> None:
    if not phone or phone in ('N/A', ''):
        return
    if not phone.startswith('+31'):
        raise GuardrailError(
            f"Netherlands phone must use +31 country code format, got: '{phone}'"
        )
