from db.pii_columns import is_pii_column


def test_snake_case_pii_columns_detected():
    assert is_pii_column("email_address")
    assert is_pii_column("customer_email")
    assert is_pii_column("ssn")
    assert is_pii_column("phone_number")


def test_camel_case_pii_columns_detected():
    assert is_pii_column("customerEmail")
    assert is_pii_column("clientSSN")
    assert is_pii_column("userPhoneNumber")


def test_non_pii_substring_matches_not_flagged():
    assert not is_pii_column("voicemail")
    assert not is_pii_column("cousin_id")
    assert not is_pii_column("basin_area")


def test_non_pii_columns_not_flagged():
    assert not is_pii_column("city")
    assert not is_pii_column("postal_code")
    assert not is_pii_column("listing_price")
