"""
Guardrail validation functions for CRUD tool operations.
All functions raise GuardrailError on violation, return None on pass.
No DB calls or side effects — pure validation only.
"""


class GuardrailError(Exception):
    pass


VALID_COUNTRIES = {'CA', 'USA', 'NL', 'MX', 'JP'}

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


def check_country(country: str) -> None:
    if country and country not in VALID_COUNTRIES:
        raise GuardrailError(
            f"Invalid country '{country}'. Must be one of: {', '.join(sorted(VALID_COUNTRIES))}"
        )


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
