"""Unified schema discovery — dispatches to SQLite or PostgreSQL based on DB_BACKEND.

Import from here rather than from db.sqlite_schema_discovery or db.pg_schema_discovery
so call sites work regardless of the configured backend.
"""
from db.connection import get_backend


def _impl():
    backend = get_backend()
    if backend == "postgres":
        import db.pg_schema_discovery as m
    else:
        import db.sqlite_schema_discovery as m
    return m


def get_table_schema(db_path: str, table_name: str, schema: str = "public"):
    return _impl().get_table_schema(db_path, table_name, schema)


def get_all_schemas(db_path: str, schema: str = "public"):
    return _impl().get_all_schemas(db_path, schema)


def get_available_schemas(db_path: str):
    """List all available schemas (Postgres only; SQLite returns ['main'])."""
    impl = _impl()
    if hasattr(impl, 'get_available_schemas'):
        return impl.get_available_schemas(db_path)
    return ["main"]  # SQLite only has one schema


def format_schema_for_prompt(db_path: str, schema: str = "public") -> str:
    return _impl().format_schema_for_prompt(db_path, schema)


def get_table_columns(db_path: str, table_name: str, schema: str = "public"):
    return _impl().get_table_columns(db_path, table_name, schema)


def get_column_metadata(db_path: str, table_name: str):
    return _impl().get_column_metadata(db_path, table_name)


def get_gap_detection(db_path: str, domain: str):
    return _impl().get_gap_detection(db_path, domain)
