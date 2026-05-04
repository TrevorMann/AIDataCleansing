def infer_column_profile(column_name: str) -> dict:
    """Return a best-effort generic profile guess for a column name."""
    key = column_name.lower()

    if key == "id":
        return _profile("identifier", 0.99, validator="primary_key")
    if key.endswith("_id"):
        return _profile("foreign_key", 0.95, validator="foreign_key")
    if key == "name":
        return _profile("person_name", 0.85, normalizer="proper_case")
    if key == "age":
        return _profile("integer_measure", 0.95, validator="age_range")
    if key == "city":
        return _profile("locality", 0.90, normalizer="proper_case")
    if key == "address":
        return _profile("street_address", 0.90, normalizer="street_address")
    if key == "postal_code":
        return _profile("postal_code", 0.95, normalizer="postal_code")
    if key == "municipality":
        return _profile("district", 0.75, normalizer="proper_case")
    if key == "state_province":
        return _profile("administrative_area", 0.90, normalizer="region_name")
    if key == "country":
        return _profile("country", 0.95, normalizer="country_name_or_code")
    if key == "phone":
        return _profile("phone_number", 0.95, normalizer="phone")
    if key in {"validation_notes", "reason", "description"}:
        return _profile("free_text", 0.70)
    if key in {"flag_type", "severity", "rule_applied"}:
        return _profile("categorical_label", 0.80)
    if key.endswith("_at"):
        return _profile("timestamp", 0.95, validator="timestamp")
    if key.endswith("_by"):
        return _profile("actor_label", 0.85)
    return _profile("generic_text", 0.30)


def _profile(
    inferred_role: str,
    role_confidence: float,
    *,
    normalizer: str | None = None,
    validator: str | None = None,
    notes: str | None = None,
    is_sensitive: int = 0,
) -> dict:
    return {
        "inferred_role": inferred_role,
        "role_confidence": role_confidence,
        "normalizer": normalizer,
        "validator": validator,
        "notes": notes,
        "is_sensitive": is_sensitive,
    }
