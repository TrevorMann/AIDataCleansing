import sqlite3

from db.profile_inference import infer_column_profile
from db.sqlite_municipality_schema import create_municipality_tables, add_columns_to_listings


def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Initialize the database with schema if it doesn't exist."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
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
        """
    )

    cursor.execute(
        """
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
        """
    )

    cursor.execute(
        """
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
        """
    )

    cursor.execute(
        """
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
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_flags_unresolved ON flags(resolved_at) WHERE resolved_at IS NULL
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS column_metadata (
            table_name  TEXT NOT NULL,
            column_name TEXT NOT NULL,
            description TEXT,
            PRIMARY KEY (table_name, column_name)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS column_profiles (
            table_name      TEXT NOT NULL,
            column_name     TEXT NOT NULL,
            inferred_role   TEXT,
            role_confidence REAL,
            normalizer      TEXT,
            validator       TEXT,
            is_sensitive    INTEGER DEFAULT 0,
            notes           TEXT,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (table_name, column_name)
        )
        """
    )

    _seed_column_metadata(cursor)
    _seed_column_profiles(cursor)

    conn.commit()

    # Create municipality normalization tables
    create_municipality_tables(conn)

    # Add municipality columns to raw_data and cleaned_data
    add_columns_to_listings(conn)

    # Create seeder support tables (spell corrections, query memory, plan cache)
    create_seeder_tables(conn)

    conn.close()


def create_seeder_tables(conn) -> None:
    """Create seeder-populated tables with SQLite-compatible DDL.

    Mirrors db/migrations/003–005 without Postgres-specific types
    (SERIAL → INTEGER PRIMARY KEY, TIMESTAMPTZ → TEXT, JSONB → TEXT, NOW() → CURRENT_TIMESTAMP).
    Safe to call multiple times — all statements use IF NOT EXISTS.
    """
    cur = conn.cursor()

    # 003 spell_corrections
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spell_corrections (
            wrong      TEXT NOT NULL,
            domain     TEXT NOT NULL,
            right      TEXT NOT NULL,
            source     TEXT NOT NULL DEFAULT 'manual_seed',
            confidence REAL NOT NULL DEFAULT 1.0,
            added_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (wrong, domain)
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_spell_corr_domain ON spell_corrections(domain)"
    )

    # 004 query_pattern_memory
    cur.execute("""
        CREATE TABLE IF NOT EXISTS query_pattern_memory (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            domain            TEXT NOT NULL,
            gap_type          TEXT NOT NULL,
            query_template    TEXT NOT NULL,
            success_count     INTEGER NOT NULL DEFAULT 0,
            failure_count     INTEGER NOT NULL DEFAULT 0,
            last_used_at      TEXT,
            sample_resolution TEXT,
            UNIQUE (domain, gap_type, query_template)
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_qpm_domain_gap ON query_pattern_memory(domain, gap_type)"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS source_registry (
            domain_key    TEXT NOT NULL,
            url_host      TEXT NOT NULL,
            trust_score   REAL NOT NULL DEFAULT 0.5,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            license_notes TEXT,
            PRIMARY KEY (domain_key, url_host)
        )
    """)

    # 005 plan_cache
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plan_cache (
            signature  TEXT PRIMARY KEY,
            domain     TEXT NOT NULL,
            plan       TEXT NOT NULL,
            reasoning  TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_cache_expires ON plan_cache(expires_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_cache_domain ON plan_cache(domain)"
    )

    conn.commit()


def _seed_column_metadata(cursor) -> None:
    """Insert default column descriptions if they don't already exist."""
    defaults = [
        ("raw_data", "name", "Full name of the contact in Proper Case"),
        ("raw_data", "age", "Integer between 1 and 120"),
        ("raw_data", "city", "City name in Proper Case"),
        ("raw_data", "address", "Street address with standardized abbreviations (Street, Avenue, Road)"),
        ("raw_data", "postal_code", "Postal/ZIP code in the format for the record country"),
        ("raw_data", "municipality", "Real estate neighbourhood name (e.g. North York, Upper East Side)"),
        ("raw_data", "state_province", "Full state or province name (e.g. Ontario, New York)"),
        ("raw_data", "country", "Must be one of: CA, USA, NL, MX, JP"),
        ("raw_data", "phone", "Phone number in country-appropriate format"),
        ("cleaned_data", "name", "Cleaned full name in Proper Case"),
        ("cleaned_data", "age", "Integer between 1 and 120"),
        ("cleaned_data", "city", "Cleaned city name in Proper Case"),
        ("cleaned_data", "address", "Cleaned street address"),
        ("cleaned_data", "postal_code", "Validated postal/ZIP code"),
        ("cleaned_data", "municipality", "Verified real estate neighbourhood name"),
        ("cleaned_data", "state_province", "Full state or province name"),
        ("cleaned_data", "country", "Full country name (e.g. Canada, United States)"),
        ("cleaned_data", "phone", "Standardized phone number"),
        ("cleaned_data", "validation_notes", "Notes on what was changed and confidence level"),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO column_metadata (table_name, column_name, description) VALUES (?, ?, ?)",
        defaults,
    )


def _seed_column_profiles(cursor) -> None:
    columns = {
        "raw_data": [
            "id", "name", "age", "city", "address", "postal_code", "municipality",
            "state_province", "country", "phone", "imported_at", "imported_by",
        ],
        "cleaned_data": [
            "id", "raw_data_id", "name", "age", "city", "address", "postal_code",
            "municipality", "state_province", "country", "phone", "validation_notes",
            "cleaned_at", "cleaned_by",
        ],
        "audit_log": [
            "id", "raw_data_id", "cleaned_data_id", "rule_applied", "description",
            "applied_at", "applied_by",
        ],
        "flags": [
            "id", "raw_data_id", "cleaned_data_id", "flag_type", "severity", "reason",
            "raised_by", "raised_at", "resolved_at", "resolved_by", "resolution_note",
        ],
    }
    rows = []
    for table_name, table_columns in columns.items():
        for column_name in table_columns:
            profile = infer_column_profile(column_name)
            rows.append(
                (
                    table_name,
                    column_name,
                    profile["inferred_role"],
                    profile["role_confidence"],
                    profile["normalizer"],
                    profile["validator"],
                    profile["is_sensitive"],
                    profile["notes"],
                )
            )
    cursor.executemany(
        """
        INSERT OR IGNORE INTO column_profiles (
            table_name, column_name, inferred_role, role_confidence,
            normalizer, validator, is_sensitive, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
