"""Unified DB helpers — dispatches to SQLite or PostgreSQL based on DB_BACKEND.

Import from here rather than from db.sqlite_helpers or db.pg_helpers so call sites
work regardless of the configured backend. Mirrors the dispatch pattern in
db.schema_discovery.

Only the functions implemented by *both* backends are re-exported here. SQLite-only
helpers (boundary/cache/city-municipality seeding) must be imported from
db.sqlite_helpers directly.
"""
from db.connection import get_backend


def _impl():
    backend = get_backend()
    if backend == "postgres":
        import db.pg_helpers as m
    else:
        import db.sqlite_helpers as m
    return m


# raw_data / cleaned_data CRUD
def insert_raw_data(*args, **kwargs):
    return _impl().insert_raw_data(*args, **kwargs)


def get_raw_data_by_id(*args, **kwargs):
    return _impl().get_raw_data_by_id(*args, **kwargs)


def get_all_raw_data(*args, **kwargs):
    return _impl().get_all_raw_data(*args, **kwargs)


def insert_cleaned_data(*args, **kwargs):
    return _impl().insert_cleaned_data(*args, **kwargs)


def get_cleaned_data_for_raw(*args, **kwargs):
    return _impl().get_cleaned_data_for_raw(*args, **kwargs)


def update_raw_data(*args, **kwargs):
    return _impl().update_raw_data(*args, **kwargs)


def update_cleaned_data(*args, **kwargs):
    return _impl().update_cleaned_data(*args, **kwargs)


def delete_raw_data(*args, **kwargs):
    return _impl().delete_raw_data(*args, **kwargs)


def query_records(*args, **kwargs):
    return _impl().query_records(*args, **kwargs)


def get_already_cleaned_ids(*args, **kwargs):
    return _impl().get_already_cleaned_ids(*args, **kwargs)


# audit log
def insert_audit_log(*args, **kwargs):
    return _impl().insert_audit_log(*args, **kwargs)


def get_audit_log_for_record(*args, **kwargs):
    return _impl().get_audit_log_for_record(*args, **kwargs)


# flags
def insert_flag(*args, **kwargs):
    return _impl().insert_flag(*args, **kwargs)


def update_flag_resolution(*args, **kwargs):
    return _impl().update_flag_resolution(*args, **kwargs)


def query_flags(*args, **kwargs):
    return _impl().query_flags(*args, **kwargs)


# generic table ops
def insert_row(*args, **kwargs):
    return _impl().insert_row(*args, **kwargs)


def update_row(*args, **kwargs):
    return _impl().update_row(*args, **kwargs)


def query_rows(*args, **kwargs):
    return _impl().query_rows(*args, **kwargs)


# column profiles
def get_column_profiles(*args, **kwargs):
    return _impl().get_column_profiles(*args, **kwargs)


def upsert_column_profile(*args, **kwargs):
    return _impl().upsert_column_profile(*args, **kwargs)
