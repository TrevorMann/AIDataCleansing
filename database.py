import sqlite3
import os

def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    return conn


def init_db(db_path: str) -> None:
    """Initialize the database with schema if it doesn't exist."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # raw_data table - stores imported data
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS raw_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        age INTEGER,
        city TEXT,
        address TEXT,
        postal_code TEXT,
        municipality TEXT,
        state_province TEXT,
        country TEXT,
        phone TEXT,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        imported_by TEXT
    )
    ''')

    # cleaned_data table - stores cleaned results
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cleaned_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_data_id INTEGER NOT NULL,
        name TEXT,
        age INTEGER,
        city TEXT,
        address TEXT,
        postal_code TEXT,
        municipality TEXT,
        state_province TEXT,
        country TEXT,
        phone TEXT,
        validation_notes TEXT,
        cleaned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        cleaned_by TEXT,
        FOREIGN KEY (raw_data_id) REFERENCES raw_data(id)
    )
    ''')

    # audit_log table - tracks transformations
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_data_id INTEGER NOT NULL,
        cleaned_data_id INTEGER,
        rule_applied TEXT,
        description TEXT,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        applied_by TEXT,
        FOREIGN KEY (raw_data_id) REFERENCES raw_data(id),
        FOREIGN KEY (cleaned_data_id) REFERENCES cleaned_data(id)
    )
    ''')

    # flags table — queryable record of unresolved or noteworthy issues per record.
    # Schema documented in: docs/superpowers/specs/2026-04-27-data-cleaning-c-hybrid-refactor-design.md §5.3
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS flags (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_data_id     INTEGER NOT NULL,
        cleaned_data_id INTEGER,
        flag_type       TEXT NOT NULL,
        severity        TEXT NOT NULL,
        reason          TEXT NOT NULL,
        raised_by       TEXT NOT NULL,
        raised_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at     TIMESTAMP,
        resolved_by     TEXT,
        resolution_note TEXT,
        FOREIGN KEY (raw_data_id) REFERENCES raw_data(id),
        FOREIGN KEY (cleaned_data_id) REFERENCES cleaned_data(id)
    )
    ''')
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_flags_unresolved ON flags(resolved_at) WHERE resolved_at IS NULL
    ''')

    # column_metadata table - human-readable descriptions for each column,
    # used to build Claude tool schemas at runtime without hardcoding
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS column_metadata (
        table_name  TEXT NOT NULL,
        column_name TEXT NOT NULL,
        description TEXT,
        PRIMARY KEY (table_name, column_name)
    )
    ''')

    _seed_column_metadata(cursor)

    conn.commit()
    conn.close()


def _seed_column_metadata(cursor) -> None:
    """Insert default column descriptions if they don't already exist."""
    defaults = [
        ('raw_data',      'name',           'Full name of the contact in Proper Case'),
        ('raw_data',      'age',            'Integer between 1 and 120'),
        ('raw_data',      'city',           'City name in Proper Case'),
        ('raw_data',      'address',        'Street address with standardized abbreviations (Street, Avenue, Road)'),
        ('raw_data',      'postal_code',    'Postal/ZIP code in the format for the record country'),
        ('raw_data',      'municipality',   'Real estate neighbourhood name (e.g. North York, Upper East Side)'),
        ('raw_data',      'state_province', 'Full state or province name (e.g. Ontario, New York)'),
        ('raw_data',      'country',        'Must be one of: CA, USA, NL, MX, JP'),
        ('raw_data',      'phone',          'Phone number in country-appropriate format'),
        ('cleaned_data',  'name',           'Cleaned full name in Proper Case'),
        ('cleaned_data',  'age',            'Integer between 1 and 120'),
        ('cleaned_data',  'city',           'Cleaned city name in Proper Case'),
        ('cleaned_data',  'address',        'Cleaned street address'),
        ('cleaned_data',  'postal_code',    'Validated postal/ZIP code'),
        ('cleaned_data',  'municipality',   'Verified real estate neighbourhood name'),
        ('cleaned_data',  'state_province', 'Full state or province name'),
        ('cleaned_data',  'country',        'Full country name (e.g. Canada, United States)'),
        ('cleaned_data',  'phone',          'Standardized phone number'),
        ('cleaned_data',  'validation_notes', 'Notes on what was changed and confidence level'),
    ]
    cursor.executemany(
        'INSERT OR IGNORE INTO column_metadata (table_name, column_name, description) VALUES (?, ?, ?)',
        defaults,
    )
