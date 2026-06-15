from typing import Any

from db.connection import get_connection, get_pg_dsn
from db.profile_inference import infer_column_profile
from db.pg_vector import init_vector_tables


def get_db_connection(_: str) -> Any:
    """Get a connection to the PostgreSQL database."""
    return get_connection(get_pg_dsn())


def init_db(db_path: str, schema: str = "data_details") -> None:
    """Initialize the PostgreSQL database with framework schema.

    Creates a schema (default: 'data_details') for all framework tables.
    Domains point to their own data schemas elsewhere.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()

        # Create the schema if it doesn't exist
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.raw_data (
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
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.cleaned_data (
                id SERIAL PRIMARY KEY,
                raw_data_id INTEGER NOT NULL REFERENCES {schema}.raw_data(id),
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
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.audit_log (
                id SERIAL PRIMARY KEY,
                raw_data_id INTEGER NOT NULL REFERENCES {schema}.raw_data(id),
                cleaned_data_id INTEGER REFERENCES {schema}.cleaned_data(id),
                rule_applied TEXT,
                description TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                applied_by TEXT
            )
            """
        )
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.flags (
                id              SERIAL PRIMARY KEY,
                raw_data_id     INTEGER NOT NULL REFERENCES {schema}.raw_data(id),
                cleaned_data_id INTEGER REFERENCES {schema}.cleaned_data(id),
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
            f"""
            CREATE INDEX IF NOT EXISTS idx_flags_unresolved
            ON {schema}.flags(resolved_at) WHERE resolved_at IS NULL
            """
        )
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.column_metadata (
                domain      TEXT NOT NULL DEFAULT 'base',
                table_name  TEXT NOT NULL,
                column_name TEXT NOT NULL,
                description TEXT,
                PRIMARY KEY (domain, table_name, column_name)
            )
            """
        )
        # Migration: add domain column if existing install lacks it
        cursor.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = '{schema}' AND table_name='column_metadata' AND column_name='domain'
                ) THEN
                    ALTER TABLE {schema}.column_metadata ADD COLUMN domain TEXT NOT NULL DEFAULT 'base';
                END IF;
            END $$
        """)
        # Migration 006: annotation provenance fields
        cursor.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = '{schema}' AND table_name='column_metadata' AND column_name='is_llm_generated'
                ) THEN
                    ALTER TABLE {schema}.column_metadata
                        ADD COLUMN is_llm_generated BOOLEAN   DEFAULT FALSE,
                        ADD COLUMN confidence        FLOAT     DEFAULT NULL,
                        ADD COLUMN generated_at      TIMESTAMP DEFAULT NULL;
                END IF;
            END $$
        """)
        # Migration 007: per-field gap detection config
        cursor.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = '{schema}' AND table_name='column_metadata' AND column_name='gap_detection'
                ) THEN
                    ALTER TABLE {schema}.column_metadata
                        ADD COLUMN gap_detection JSONB DEFAULT NULL;
                END IF;
            END $$
        """)
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.column_profiles (
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

        _create_municipality_tables(cursor, schema)
        _add_municipality_columns_to_listings(cursor, schema)
        _seed_column_metadata(cursor, schema)
        _seed_column_profiles(cursor, schema)
        init_vector_tables(conn, schema)
        conn.commit()
    finally:
        conn.close()


def _create_municipality_tables(cursor, schema: str) -> None:
    """Create municipality normalization tables for PostgreSQL."""
    # geo_boundary_reference
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.geo_boundary_reference (
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
        f"""
        CREATE INDEX IF NOT EXISTS idx_geo_boundary_municipality_province
        ON {schema}.geo_boundary_reference(normalized_municipality, province)
        """
    )

    # city_municipality_map
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.city_municipality_map (
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
        f"""
        CREATE INDEX IF NOT EXISTS idx_city_map_city_province
        ON {schema}.city_municipality_map(city_name, province, country)
        """
    )

    # fsa_municipality_mapping
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.fsa_municipality_mapping (
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
        f"""
        CREATE INDEX IF NOT EXISTS idx_fsa_mapping_fsa_province
        ON {schema}.fsa_municipality_mapping(fsa, province, country)
        """
    )

    # municipality_lookup_cache
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.municipality_lookup_cache (
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
        f"""
        CREATE INDEX IF NOT EXISTS idx_cache_lookup_key
        ON {schema}.municipality_lookup_cache(lookup_key)
        """
    )

    # property_migration_history
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.property_migration_history (
            id SERIAL PRIMARY KEY,
            raw_data_id INTEGER NOT NULL REFERENCES {schema}.raw_data(id),
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
        f"""
        CREATE INDEX IF NOT EXISTS idx_property_migration_raw_data_id
        ON {schema}.property_migration_history(raw_data_id)
        """
    )


def _add_municipality_columns_to_listings(cursor, schema: str) -> None:
    """Add municipality normalization columns to raw_data and cleaned_data tables."""
    # Add to raw_data
    for col_name, col_type in [
        ("normalized_municipality", "TEXT"),
        ("municipality_ref_id", "INTEGER"),
        ("confidence_score", "REAL"),
        ("normalization_status", "TEXT DEFAULT 'pending'"),
    ]:
        cursor.execute(f"""
            ALTER TABLE {schema}.raw_data ADD COLUMN IF NOT EXISTS {col_name} {col_type}
        """)

    # Add to cleaned_data
    for col_name, col_type in [
        ("normalized_municipality", "TEXT"),
        ("municipality_ref_id", "INTEGER"),
        ("confidence_score", "REAL"),
        ("normalization_status", "TEXT DEFAULT 'pending'"),
    ]:
        cursor.execute(f"""
            ALTER TABLE {schema}.cleaned_data ADD COLUMN IF NOT EXISTS {col_name} {col_type}
        """)


def add_source_column_to_cache(conn: Any, schema: str = "data_details") -> None:
    """Add source column to municipality_lookup_cache for tracking data provenance.

    Migration: adds source TEXT column if not present.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            ALTER TABLE {schema}.municipality_lookup_cache ADD COLUMN IF NOT EXISTS source TEXT
        """)
        conn.commit()
    except Exception:
        pass


def _seed_column_metadata(cursor, schema: str) -> None:
    base = [
        ("base", "raw_data", "name",           "Full name of contact — may have typos or wrong case. Clean to Proper Case."),
        ("base", "raw_data", "age",            "Age in years (integer 1-120). Non-numeric values are invalid."),
        ("base", "raw_data", "city",           "City name — may have typos or wrong case. Clean to Proper Case."),
        ("base", "raw_data", "address",        "Street address — expand abbreviations: St→Street, Ave→Avenue, Rd→Road, Blvd→Boulevard. Do not alter proper nouns."),
        ("base", "raw_data", "postal_code",    "Postal/ZIP code. Format depends on country (e.g. A1A 1A1 for Canada, 5 digits for USA). Validate format against country; do not guess if uncertain."),
        ("base", "raw_data", "municipality",   "Municipality or district name."),
        ("base", "raw_data", "state_province", "Full state or province name (e.g. Ontario, New York). Not abbreviated."),
        ("base", "raw_data", "country",        "Country as provided in raw input — may be a code (CA, US), abbreviation (USA), or full name. Standardize to full name in cleaned output (e.g. Canada, United States, Netherlands, Mexico, Japan)."),
        ("base", "raw_data", "phone",          "Phone number — format per country standard. Leave unchanged if country unknown."),
        ("base", "cleaned_data", "name",             "Cleaned full name in Proper Case."),
        ("base", "cleaned_data", "age",              "Validated age (integer 1-120)."),
        ("base", "cleaned_data", "city",             "Cleaned city name in Proper Case."),
        ("base", "cleaned_data", "address",          "Cleaned street address with standardized abbreviations."),
        ("base", "cleaned_data", "postal_code",      "Validated postal/ZIP code in country-standard format."),
        ("base", "cleaned_data", "municipality",     "Cleaned municipality or district name."),
        ("base", "cleaned_data", "state_province",   "Full state or province name."),
        ("base", "cleaned_data", "country",          "Full country name (e.g. Canada, United States, Netherlands, Mexico, Japan)."),
        ("base", "cleaned_data", "phone",            "Phone number in country-standard format."),
        ("base", "cleaned_data", "validation_notes", "Notes on every decision: what changed, what was uncertain, confidence level. Document each field."),
    ]
    cursor.executemany(
        f"""
        INSERT INTO {schema}.column_metadata (domain, table_name, column_name, description)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (domain, table_name, column_name) DO NOTHING
        """,
        base,
    )


def _seed_column_profiles(cursor, schema: str) -> None:
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
        f"""
        INSERT INTO {schema}.column_profiles (
            table_name, column_name, inferred_role, role_confidence,
            normalizer, validator, is_sensitive, notes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (table_name, column_name) DO NOTHING
        """,
        rows,
    )


if __name__ == "__main__":
    init_db(None)
    print("✓ Database initialized in 'data_details' schema")
