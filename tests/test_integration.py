import os
import tempfile
from database import init_db
from db_helpers import insert_raw_data, get_all_raw_data, insert_cleaned_data, insert_audit_log, get_audit_log_for_record

def test_full_cleaning_workflow():
    """Test: raw data → cleaned data → audit trail"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)

        # Step 1: Insert raw data
        raw_id = insert_raw_data(
            db_path,
            name="john doe",
            age=35,
            city="toronto",
            address="123 Main St",
            postal_code="M5V1A1",
            municipality="N/A",
            state_province="ON",
            country="CA",
            phone="416-555-0123",
            imported_by="csv_import"
        )

        # Step 2: Simulate Claude cleaning
        cleaned_id = insert_cleaned_data(
            db_path,
            raw_data_id=raw_id,
            name="John Doe",
            age=35,
            city="Toronto",
            address="123 Main Street",
            postal_code="M5V 1A1",
            municipality="Toronto",
            state_province="Ontario",
            country="Canada",
            phone="(416) 555-0123",
            validation_notes="Name capitalized, city standardized, address expanded, postal code formatted, country/province expanded",
            cleaned_by="claude-haiku-4.5"
        )

        # Step 3: Log transformations
        insert_audit_log(
            db_path,
            raw_data_id=raw_id,
            cleaned_data_id=cleaned_id,
            rule_applied="phone_formatted",
            description="416-555-0123 → (416) 555-0123",
            applied_by="claude-haiku-4.5"
        )

        insert_audit_log(
            db_path,
            raw_data_id=raw_id,
            cleaned_data_id=cleaned_id,
            rule_applied="address_standardized",
            description="Main St → Main Street",
            applied_by="claude-haiku-4.5"
        )

        # Step 4: Verify data flow
        all_raw = get_all_raw_data(db_path)
        assert len(all_raw) == 1
        assert all_raw[0]['name'] == "john doe"  # raw unchanged

        audit_entries = get_audit_log_for_record(db_path, raw_id)
        assert len(audit_entries) == 2
        assert audit_entries[0]['rule_applied'] == "phone_formatted"
        assert audit_entries[1]['rule_applied'] == "address_standardized"
