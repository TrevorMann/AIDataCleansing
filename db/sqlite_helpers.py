from typing import Dict, List, Optional

from db.sqlite_init import get_db_connection
from db.sqlite_schema_discovery import get_all_schemas, get_table_schema
from db.schema_config import get_framework_schema


_AUTO_MANAGED_COLUMNS = {"id", "imported_at", "cleaned_at", "applied_at", "raised_at", "resolved_at", "updated_at"}


def _get_schema_map(db_path: str, table: str) -> Dict[str, Dict]:
    schema = get_table_schema(db_path, table)
    if not schema:
        raise ValueError(f"Unknown table '{table}'")
    return {column["name"]: column for column in schema}


def _validate_fields(db_path: str, table: str, fields: Dict, *, protected_fields: set[str] | None = None) -> None:
    if not fields:
        raise ValueError("No fields specified")
    schema_map = _get_schema_map(db_path, table)
    unknown = set(fields) - set(schema_map)
    if unknown:
        raise ValueError(f"Unknown columns for {table}: {', '.join(sorted(unknown))}")
    protected = _AUTO_MANAGED_COLUMNS | (protected_fields or set())
    bad = set(fields) & protected
    if bad:
        raise ValueError(f"Cannot write protected fields: {', '.join(sorted(bad))}")


def insert_raw_data(
    db_path: str,
    name: str,
    age: Optional[int] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    postal_code: Optional[str] = None,
    municipality: Optional[str] = None,
    state_province: Optional[str] = None,
    country: Optional[str] = None,
    phone: Optional[str] = None,
    imported_by: Optional[str] = None,
) -> int:
    """Insert a raw data record. Returns the row ID."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO raw_data (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_raw_data_by_id(
    db_path: str,
    raw_data_id: int, schema: str = None) -> Optional[Dict]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM raw_data WHERE id = ?", (raw_data_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_raw_data(
    db_path: str,
    schema: str = None
) -> List[Dict]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM raw_data ORDER BY imported_at DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def insert_cleaned_data(
    db_path: str,
    raw_data_id: int,
    name: Optional[str] = None,
    age: Optional[int] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    postal_code: Optional[str] = None,
    municipality: Optional[str] = None,
    state_province: Optional[str] = None,
    country: Optional[str] = None,
    phone: Optional[str] = None,
    validation_notes: Optional[str] = None,
    cleaned_by: Optional[str] = None,
    normalized_municipality: Optional[str] = None,
    confidence_score: Optional[float] = None,
    normalization_status: Optional[str] = None,
    schema: str = None,
) -> int:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO cleaned_data (raw_data_id, name, age, city, address, postal_code, municipality, state_province, country, phone, validation_notes, cleaned_by, normalized_municipality, confidence_score, normalization_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_data_id,
                name,
                age,
                city,
                address,
                postal_code,
                municipality,
                state_province,
                country,
                phone,
                validation_notes,
                cleaned_by,
                normalized_municipality,
                confidence_score,
                normalization_status,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def insert_audit_log(
    db_path: str,
    raw_data_id: int,
    cleaned_data_id: Optional[int] = None,
    rule_applied: Optional[str] = None,
    description: Optional[str] = None,
    applied_by: Optional[str] = None,
) -> int:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_log (raw_data_id, cleaned_data_id, rule_applied, description, applied_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (raw_data_id, cleaned_data_id, rule_applied, description, applied_by),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_audit_log_for_record(db_path: str, raw_data_id: int) -> List[Dict]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_log WHERE raw_data_id = ? ORDER BY applied_at", (raw_data_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_raw_data(db_path: str, record_id: int, fields: Dict) -> bool:
    return update_row(db_path, "raw_data", record_id, fields, protected_fields={"imported_at"})


def update_cleaned_data(db_path: str, record_id: int, fields: Dict) -> bool:
    return update_row(db_path, "cleaned_data", record_id, fields, protected_fields={"raw_data_id", "cleaned_at"})


def delete_raw_data(db_path: str, record_id: int) -> bool:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM raw_data WHERE id = ?", (record_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_cleaned_data_for_raw(
    db_path: str,
    raw_data_id: int, schema: str = None) -> List[Dict]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cleaned_data WHERE raw_data_id = ?", (raw_data_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def query_records(
    db_path: str,
    table: str = "raw_data",
    filters: Optional[Dict] = None,
    limit: int = 50,
) -> List[Dict]:
    return query_rows(db_path, table=table, filters=filters, limit=limit)


def insert_flag(
    db_path: str,
    raw_data_id: int,
    flag_type: str,
    severity: str,
    reason: str,
    raised_by: str,
    cleaned_data_id: Optional[int] = None,
    schema: str = None,
) -> int:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO flags (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_flag_resolution(
    db_path: str,
    flag_id: int,
    resolved_by: str,
    note: Optional[str] = None,
) -> bool:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE flags SET resolved_at = CURRENT_TIMESTAMP, resolved_by = ?, resolution_note = ?
            WHERE id = ? AND resolved_at IS NULL
            """,
            (resolved_by, note, flag_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def query_flags(
    db_path: str,
    only_unresolved: bool = True,
    raw_data_id: Optional[int] = None,
    flag_type: Optional[str] = None,
    limit: int = 100,
    schema: str = None,
) -> List[Dict]:
    where = []
    params: list = []
    if only_unresolved:
        where.append("resolved_at IS NULL")
    if raw_data_id is not None:
        where.append("raw_data_id = ?")
        params.append(raw_data_id)
    if flag_type is not None:
        where.append("flag_type = ?")
        params.append(flag_type)

    sql = "SELECT * FROM flags"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY raised_at DESC LIMIT ?"
    params.append(limit)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_already_cleaned_ids(db_path: str) -> set[int]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT raw_data_id FROM cleaned_data")
        return {int(row[0]) for row in cursor.fetchall()}
    finally:
        conn.close()


def insert_row(
    db_path: str,
    table: str,
    values: Dict,
    *,
    protected_fields: set[str] | None = None,
) -> int:
    _validate_fields(db_path, table, values, protected_fields=protected_fields)
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, [values[column] for column in columns])
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def update_row(
    db_path: str,
    table: str,
    record_id: int,
    fields: Dict,
    *,
    id_column: str = "id",
    protected_fields: set[str] | None = None,
) -> bool:
    schema_map = _get_schema_map(db_path, table)
    if id_column not in schema_map:
        raise ValueError(f"Unknown identifier column '{id_column}' for table '{table}'")
    _validate_fields(db_path, table, fields, protected_fields=(protected_fields or set()) | {id_column})

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [record_id]

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE {table} SET {set_clause} WHERE {id_column} = ?", values)
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def query_rows(
    db_path: str,
    *,
    table: str,
    filters: Optional[Dict] = None,
    limit: int = 50,
) -> List[Dict]:
    valid_tables = set(get_all_schemas(db_path))
    if table not in valid_tables:
        raise ValueError(f"Invalid table '{table}'. Must be one of: {', '.join(sorted(valid_tables))}")

    schema_map = _get_schema_map(db_path, table)
    where_clauses = []
    params = []
    if filters:
        unknown = set(filters) - set(schema_map)
        if unknown:
            raise ValueError(f"Unknown filters for {table}: {', '.join(sorted(unknown))}")
        for col, val in filters.items():
            where_clauses.append(f"{col} = ?")
            params.append(val)

    sql = f"SELECT * FROM {table}"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " LIMIT ?"
    params.append(limit)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_column_profiles(db_path: str, table_name: str) -> Dict[str, Dict]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT column_name, inferred_role, role_confidence, normalizer, validator, is_sensitive, notes, updated_at
            FROM column_profiles
            WHERE table_name = ?
            """,
            (table_name,),
        )
        return {
            row["column_name"]: {
                "inferred_role": row["inferred_role"],
                "role_confidence": row["role_confidence"],
                "normalizer": row["normalizer"],
                "validator": row["validator"],
                "is_sensitive": row["is_sensitive"],
                "notes": row["notes"],
                "updated_at": row["updated_at"],
            }
            for row in cursor.fetchall()
        }
    finally:
        conn.close()


def upsert_column_profile(
    db_path: str,
    table_name: str,
    column_name: str,
    *,
    inferred_role: str,
    role_confidence: float,
    normalizer: str | None = None,
    validator: str | None = None,
    is_sensitive: int = 0,
    notes: str | None = None,
) -> None:
    _get_schema_map(db_path, table_name)
    if column_name not in _get_schema_map(db_path, table_name):
        raise ValueError(f"Unknown column '{column_name}' for table '{table_name}'")

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO column_profiles (
                table_name, column_name, inferred_role, role_confidence,
                normalizer, validator, is_sensitive, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(table_name, column_name) DO UPDATE SET
                inferred_role = excluded.inferred_role,
                role_confidence = excluded.role_confidence,
                normalizer = excluded.normalizer,
                validator = excluded.validator,
                is_sensitive = excluded.is_sensitive,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                table_name,
                column_name,
                inferred_role,
                role_confidence,
                normalizer,
                validator,
                is_sensitive,
                notes,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# Municipality CRUD helpers


def insert_boundary_reference(
    db_path: str,
    normalized_municipality: str,
    province: str,
    valid_from: str,
    boundary_polygon: Optional[str] = None,
    valid_to: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    """Insert a geographic boundary reference for a municipality.

    Args:
        db_path: Path to the SQLite database
        normalized_municipality: The canonical municipality name
        province: Province/state name
        valid_from: Start date in YYYY-MM-DD format
        boundary_polygon: WKT polygon boundary (optional)
        valid_to: End date in YYYY-MM-DD format (optional)
        source: Source of the boundary data (optional)

    Returns:
        The ID of the inserted row
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO geo_boundary_reference
            (normalized_municipality, province, boundary_polygon, valid_from, valid_to, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (normalized_municipality, province, boundary_polygon, valid_from, valid_to, source),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_boundary_by_fsa_and_year(
    db_path: str,
    fsa: str,
    province: str,
    country: str,
    year: Optional[int] = None,
) -> Optional[Dict]:
    """Get the boundary reference for a postal code (FSA) in a given year.

    Args:
        db_path: Path to the SQLite database
        fsa: Postal code prefix (FSA)
        province: Province/state name
        country: Country code
        year: Year to look up (optional, uses current year if not provided)

    Returns:
        Dictionary with boundary information or None if not found
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()

        # Get the FSA mapping to find the normalized municipality
        cursor.execute(
            """
            SELECT normalized_municipality FROM fsa_municipality_mapping
            WHERE fsa = ? AND province = ? AND country = ?
            AND valid_from <= CAST(? AS DATE)
            AND (valid_to IS NULL OR valid_to >= CAST(? AS DATE))
            LIMIT 1
            """,
            (fsa, province, country, f"{year}-01-01" if year else "2024-01-01", f"{year}-12-31" if year else "2024-12-31"),
        )
        fsa_mapping = cursor.fetchone()
        if not fsa_mapping:
            return None

        normalized_municipality = fsa_mapping[0]

        # Get the boundary reference for that municipality
        cursor.execute(
            """
            SELECT * FROM geo_boundary_reference
            WHERE normalized_municipality = ? AND province = ?
            AND valid_from <= CAST(? AS DATE)
            AND (valid_to IS NULL OR valid_to >= CAST(? AS DATE))
            LIMIT 1
            """,
            (normalized_municipality, province, f"{year}-01-01" if year else "2024-01-01", f"{year}-12-31" if year else "2024-12-31"),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_cache_entry(
    db_path: str,
    lookup_key: str,
    lookup_value: str,
    query_params: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> int:
    """Insert or update a lookup cache entry.

    Args:
        db_path: Path to the SQLite database
        lookup_key: The cache key (should be unique)
        lookup_value: The cached result
        query_params: JSON string of query parameters (optional)
        expires_at: Expiration timestamp (optional)

    Returns:
        The ID of the inserted/updated row
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO municipality_lookup_cache
            (lookup_key, lookup_value, query_params, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(lookup_key) DO UPDATE SET
                lookup_value = excluded.lookup_value,
                hit_count = hit_count + 1,
                last_accessed_at = CURRENT_TIMESTAMP,
                expires_at = excluded.expires_at
            """,
            (lookup_key, lookup_value, query_params, expires_at),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_cache_hit(db_path: str, lookup_key: str) -> Optional[Dict]:
    """Get a cache entry by key if it hasn't expired.

    Args:
        db_path: Path to the SQLite database
        lookup_key: The cache key to look up

    Returns:
        Dictionary with cache entry or None if not found or expired
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM municipality_lookup_cache
            WHERE lookup_key = ?
            AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            LIMIT 1
            """,
            (lookup_key,),
        )
        row = cursor.fetchone()
        if row:
            # Update last_accessed_at
            cursor.execute(
                """
                UPDATE municipality_lookup_cache
                SET last_accessed_at = CURRENT_TIMESTAMP
                WHERE lookup_key = ?
                """,
                (lookup_key,),
            )
            conn.commit()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_city_municipality_map(
    db_path: str,
    city_name: str,
    province: str,
    country: str,
    normalized_municipality: str,
    confidence_score: float,
    source: Optional[str] = None,
    verified_at: Optional[str] = None,
) -> int:
    """Insert a mapping from a city name to a normalized municipality.

    Args:
        db_path: Path to the SQLite database
        city_name: The city name
        province: Province/state name
        country: Country code
        normalized_municipality: The canonical municipality name
        confidence_score: Confidence of the mapping (0.0-1.0)
        source: Source of the mapping (optional)
        verified_at: Verification timestamp (optional)

    Returns:
        The ID of the inserted row
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO city_municipality_map
            (city_name, province, country, normalized_municipality, confidence_score, source, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (city_name, province, country, normalized_municipality, confidence_score, source, verified_at),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_city_for_municipality(
    db_path: str,
    city_name: str,
    province: str,
    country: str,
) -> Optional[Dict]:
    """Get the normalized municipality for a city.

    Args:
        db_path: Path to the SQLite database
        city_name: The city name to look up
        province: Province/state name
        country: Country code

    Returns:
        Dictionary with the mapping or None if not found
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM city_municipality_map
            WHERE city_name = ? AND province = ? AND country = ?
            ORDER BY confidence_score DESC
            LIMIT 1
            """,
            (city_name, province, country),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
