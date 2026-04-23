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

    conn.commit()
    conn.close()
