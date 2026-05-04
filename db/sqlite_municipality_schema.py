"""Database schema for municipality normalization."""
import sqlite3


def create_municipality_tables(conn: sqlite3.Connection) -> None:
    """Create all 5 municipality normalization tables.

    Tables created:
    - geo_boundary_reference: Geographic boundaries for municipalities with temporal validity
    - city_municipality_map: Maps cities to canonical municipalities
    - fsa_municipality_mapping: Maps postal codes (FSA) to municipalities
    - municipality_lookup_cache: Caches recent municipality lookups for performance
    - property_migration_history: Tracks municipality normalization history for properties
    """
    cursor = conn.cursor()

    # 1. geo_boundary_reference: Geographic boundaries for municipalities
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS geo_boundary_reference (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_geo_boundary_valid_dates
        ON geo_boundary_reference(valid_from, valid_to)
        """
    )

    # 2. city_municipality_map: Maps cities to canonical municipalities
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS city_municipality_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_city_map_normalized
        ON city_municipality_map(normalized_municipality)
        """
    )

    # 3. fsa_municipality_mapping: Maps postal code (FSA) to municipalities
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fsa_municipality_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fsa TEXT NOT NULL,
            province TEXT NOT NULL,
            country TEXT NOT NULL,
            normalized_municipality TEXT NOT NULL,
            valid_from DATE NOT NULL,
            valid_to DATE,
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fsa_mapping_fsa_province
        ON fsa_municipality_mapping(fsa, province, country)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fsa_mapping_valid_dates
        ON fsa_municipality_mapping(valid_from, valid_to)
        """
    )

    # 4. municipality_lookup_cache: Caches recent lookups for performance
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS municipality_lookup_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cache_expires
        ON municipality_lookup_cache(expires_at)
        """
    )

    # 5. property_migration_history: Tracks normalization history
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS property_migration_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_data_id INTEGER NOT NULL,
            old_municipality TEXT,
            new_municipality TEXT NOT NULL,
            normalization_method TEXT,
            confidence_score REAL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            applied_by TEXT,
            FOREIGN KEY (raw_data_id) REFERENCES raw_data(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_property_migration_raw_data_id
        ON property_migration_history(raw_data_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_property_migration_applied_at
        ON property_migration_history(applied_at)
        """
    )

    conn.commit()


def add_columns_to_listings(conn: sqlite3.Connection) -> None:
    """Add municipality normalization columns to raw_data and cleaned_data tables.

    Adds to raw_data:
    - normalized_municipality: The canonical municipality name after normalization
    - municipality_ref_id: Reference to geo_boundary_reference table
    - confidence_score: Confidence of the normalization (0.0-1.0)
    - normalization_status: Status of normalization (pending, resolved, manual, failed)

    Adds to cleaned_data:
    - Same 4 columns as raw_data
    """
    cursor = conn.cursor()

    # Add columns to raw_data
    cursor.execute("""
        PRAGMA table_info(raw_data)
    """)
    raw_data_columns = {row[1] for row in cursor.fetchall()}

    if "normalized_municipality" not in raw_data_columns:
        cursor.execute("""
            ALTER TABLE raw_data ADD COLUMN normalized_municipality TEXT
        """)

    if "municipality_ref_id" not in raw_data_columns:
        cursor.execute("""
            ALTER TABLE raw_data ADD COLUMN municipality_ref_id INTEGER
        """)

    if "confidence_score" not in raw_data_columns:
        cursor.execute("""
            ALTER TABLE raw_data ADD COLUMN confidence_score REAL
        """)

    if "normalization_status" not in raw_data_columns:
        cursor.execute("""
            ALTER TABLE raw_data ADD COLUMN normalization_status TEXT DEFAULT 'pending'
        """)

    # Add columns to cleaned_data
    cursor.execute("""
        PRAGMA table_info(cleaned_data)
    """)
    cleaned_data_columns = {row[1] for row in cursor.fetchall()}

    if "normalized_municipality" not in cleaned_data_columns:
        cursor.execute("""
            ALTER TABLE cleaned_data ADD COLUMN normalized_municipality TEXT
        """)

    if "municipality_ref_id" not in cleaned_data_columns:
        cursor.execute("""
            ALTER TABLE cleaned_data ADD COLUMN municipality_ref_id INTEGER
        """)

    if "confidence_score" not in cleaned_data_columns:
        cursor.execute("""
            ALTER TABLE cleaned_data ADD COLUMN confidence_score REAL
        """)

    if "normalization_status" not in cleaned_data_columns:
        cursor.execute("""
            ALTER TABLE cleaned_data ADD COLUMN normalization_status TEXT DEFAULT 'pending'
        """)

    conn.commit()


def add_source_column_to_cache(conn: sqlite3.Connection) -> None:
    """Add source column to municipality_lookup_cache for tracking data provenance.

    Migration: adds source TEXT column if not present. Tracks which data loader
    populated each cache entry (e.g., 'wikipedia', 'manual', etc.)
    """
    cursor = conn.cursor()
    cursor.execute("""
        PRAGMA table_info(municipality_lookup_cache)
    """)
    cache_columns = {row[1] for row in cursor.fetchall()}

    if "source" not in cache_columns:
        cursor.execute("""
            ALTER TABLE municipality_lookup_cache ADD COLUMN source TEXT
        """)
        conn.commit()
