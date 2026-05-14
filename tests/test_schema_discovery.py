import os
import tempfile
from database import init_db
from schema_discovery import get_table_schema, get_all_schemas, format_schema_for_prompt


def test_get_table_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)

        schema = get_table_schema(db_path, "raw_data")
        assert schema is not None
        assert len(schema) > 0

        # Check that schema contains field info
        field_names = [col['name'] for col in schema]
        assert 'id' in field_names
        assert 'name' in field_names
        assert 'phone' in field_names


def test_get_all_schemas():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)

        schemas = get_all_schemas(db_path)
        assert 'raw_data' in schemas
        assert 'cleaned_data' in schemas
        assert 'audit_log' in schemas


def test_format_schema_for_prompt():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)

        formatted = format_schema_for_prompt(db_path)
        assert "raw_data" in formatted
        assert "cleaned_data" in formatted
        assert "audit_log" not in formatted  # infrastructure table — intentionally excluded
        assert isinstance(formatted, str)
