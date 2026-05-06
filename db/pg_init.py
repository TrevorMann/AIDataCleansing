from typing import Any

from db.connection import get_connection, get_pg_dsn
from db.profile_inference import infer_column_profile
from db.pg_vector import init_vector_tables


def get_db_connection(_: str) -> Any:
    """Get a connection to the PostgreSQL database."""
    return get_connection(get_pg_dsn())


def init_db(db_path: str) -> None:
    """Initialize the PostgreSQL database with schema if it doesn't exist."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_data (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                raw_data_id INTEGER NOT NULL REFERENCES raw_data(id),
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
                cleaned_by TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                raw_data_id INTEGER NOT NULL REFERENCES raw_data(id),
                cleaned_data_id INTEGER REFERENCES cleaned_data(id),
                rule_applied TEXT,
                description TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                applied_by TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS flags (
                id              SERIAL PRIMARY KEY,
                raw_data_id     INTEGER NOT NULL REFERENCES raw_data(id),
                cleaned_data_id INTEGER REFERENCES cleaned_data(id),
                flag_type       TEXT NOT NULL,
                severity        TEXT NOT NULL,
                reason          TEXT NOT NULL,
                raised_by       TEXT NOT NULL,
                raised_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at     TIMESTAMP,
                resolved_by     TEXT,
                resolution_note TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_flags_unresolved
            ON flags(resolved_at) WHERE resolved_at IS NULL
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
                role_confidence DOUBLE PRECISION,
                normalizer      TEXT,
                validator       TEXT,
                is_sensitive    BOOLEAN DEFAULT FALSE,
                notes           TEXT,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (table_name, column_name)
            )
            """
        )

        _create_municipality_tables(cursor)
        _add_municipality_columns_to_listings(cursor)
        _seed_column_metadata(cursor)
        _seed_column_profiles(cursor)
        init_vector_tables(conn)
        conn.commit()
    finally:
        conn.close()


def _create_municipality_tables(cursor) -> None:
    """Create municipality normalization tables for PostgreSQL."""
    # geo_boundary_reference
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS geo_boundary_reference (
            id SERIAL PRIMARY KEY,
            normalized_municipality TEXT NOT NULL,
            province TEXT NOT NULL,
            boundary_polygon TEXT,
            valid_from DATE NOT NULL,
            valid_to DATE,
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_geo_boundary_municipality_province
        ON geo_boundary_reference(normalized_municipality, province)
        """
    )

    # city_municipality_map
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS city_municipality_map (
            id SERIAL PRIMARY KEY,
            city_name TEXT NOT NULL,
            province TEXT NOT NULL,
            country TEXT NOT NULL,
            normalized_municipality TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            source TEXT,
            verified_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_city_map_city_province
        ON city_municipality_map(city_name, province, country)
        """
    )

    # fsa_municipality_mapping
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fsa_municipality_mapping (
            id SERIAL PRIMARY KEY,
            fsa TEXT NOT NULL,
            province TEXT NOT NULL,
            country TEXT NOT NULL,
            normalized_municipality TEXT NOT NULL,
            valid_from DATE NOT NULL,
            valid_to DATE,
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (fsa, province, country)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fsa_mapping_fsa_province
        ON fsa_municipality_mapping(fsa, province, country)
        """
    )

    # municipality_lookup_cache
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS municipality_lookup_cache (
            id SERIAL PRIMARY KEY,
            lookup_key TEXT NOT NULL UNIQUE,
            lookup_value TEXT NOT NULL,
            query_params TEXT,
            hit_count INTEGER DEFAULT 1,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cache_lookup_key
        ON municipality_lookup_cache(lookup_key)
        """
    )

    # property_migration_history
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS property_migration_history (
            id SERIAL PRIMARY KEY,
            raw_data_id INTEGER NOT NULL REFERENCES raw_data(id),
            old_municipality TEXT,
            new_municipality TEXT NOT NULL,
            normalization_method TEXT,
            confidence_score REAL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            applied_by TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_property_migration_raw_data_id
        ON property_migration_history(raw_data_id)
        """
    )


def _add_municipality_columns_to_listings(cursor) -> None:
    """Add municipality normalization columns to raw_data and cleaned_data tables."""
    # Add to raw_data
    for col_name, col_type in [
        ("normalized_municipality", "TEXT"),
        ("municipality_ref_id", "INTEGER"),
        ("confidence_score", "REAL"),
        ("normalization_status", "TEXT DEFAULT 'pending'"),
    ]:
        cursor.execute(f"""
            ALTER TABLE raw_data ADD COLUMN IF NOT EXISTS {col_name} {col_type}
        """)

    # Add to cleaned_data
    for col_name, col_type in [
        ("normalized_municipality", "TEXT"),
        ("municipality_ref_id", "INTEGER"),
        ("confidence_score", "REAL"),
        ("normalization_status", "TEXT DEFAULT 'pending'"),
    ]:
        cursor.execute(f"""
            ALTER TABLE cleaned_data ADD COLUMN IF NOT EXISTS {col_name} {col_type}
        """)


def add_source_column_to_cache(conn: Any) -> None:
    """Add source column to municipality_lookup_cache for tracking data provenance.

    Migration: adds source TEXT column if not present.
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            ALTER TABLE municipality_lookup_cache ADD COLUMN IF NOT EXISTS source TEXT
        """)
        conn.commit()
    except Exception:
        pass


def _seed_column_metadata(cursor) -> None:
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
        """
        INSERT INTO column_metadata (table_name, column_name, description)
        VALUES (%s, %s, %s)
        ON CONFLICT (table_name, column_name) DO NOTHING
        """,
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
                    bool(profile["is_sensitive"]),
                    profile["notes"],
                )
            )
    cursor.executemany(
        """
        INSERT INTO column_profiles (
            table_name, column_name, inferred_role, role_confidence,
            normalizer, validator, is_sensitive, notes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (table_name, column_name) DO NOTHING
        """,
        rows,
    )
