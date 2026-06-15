from typing import Dict, List, Optional

from db.pg_init import get_db_connection
from db.pg_schema_discovery import get_all_schemas, get_table_schema
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
    schema: str = None,
) -> int:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {schema}.raw_data (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by),
        )
        row_id = cursor.fetchone()["id"]
        conn.commit()
        return row_id
    finally:
        conn.close()


def get_raw_data_by_id(db_path: str, raw_data_id: int, schema: str = None) -> Optional[Dict]:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {schema}.raw_data WHERE id = %s", (raw_data_id,))
        return cursor.fetchone()
    finally:
        conn.close()


def get_all_raw_data(db_path: str, schema: str = None) -> List[Dict]:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {schema}.raw_data ORDER BY imported_at DESC")
        return list(cursor.fetchall())
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
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {schema}.cleaned_data (raw_data_id, name, age, city, address, postal_code, municipality, state_province, country, phone, validation_notes, cleaned_by, normalized_municipality, confidence_score, normalization_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
        row_id = cursor.fetchone()["id"]
        conn.commit()
        return row_id
    finally:
        conn.close()


def insert_audit_log(
    db_path: str,
    raw_data_id: int,
    cleaned_data_id: Optional[int] = None,
    rule_applied: Optional[str] = None,
    description: Optional[str] = None,
    applied_by: Optional[str] = None,
    schema: str = None,
) -> int:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {schema}.audit_log (raw_data_id, cleaned_data_id, rule_applied, description, applied_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (raw_data_id, cleaned_data_id, rule_applied, description, applied_by),
        )
        row_id = cursor.fetchone()["id"]
        conn.commit()
        return row_id
    finally:
        conn.close()


def get_audit_log_for_record(db_path: str, raw_data_id: int, schema: str = None) -> List[Dict]:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {schema}.audit_log WHERE raw_data_id = %s ORDER BY applied_at", (raw_data_id,))
        return list(cursor.fetchall())
    finally:
        conn.close()


def update_raw_data(db_path: str, record_id: int, fields: Dict, schema: str = None) -> bool:
    if schema is None:
        schema = get_framework_schema()
    return update_row(db_path, "raw_data", record_id, fields, protected_fields={"imported_at"}, schema=schema)


def update_cleaned_data(db_path: str, record_id: int, fields: Dict, schema: str = None) -> bool:
    if schema is None:
        schema = get_framework_schema()
    return update_row(db_path, "cleaned_data", record_id, fields, protected_fields={"raw_data_id", "cleaned_at"}, schema=schema)


def delete_raw_data(db_path: str, record_id: int, schema: str = None) -> bool:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM {schema}.raw_data WHERE id = %s", (record_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_cleaned_data_for_raw(db_path: str, raw_data_id: int, schema: str = None) -> List[Dict]:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {schema}.cleaned_data WHERE raw_data_id = %s", (raw_data_id,))
        return list(cursor.fetchall())
    finally:
        conn.close()


def query_records(
    db_path: str,
    table: str = "raw_data",
    filters: Optional[Dict] = None,
    limit: int = 50,
    schema: str = None,
) -> List[Dict]:
    if schema is None:
        schema = get_framework_schema()
    return query_rows(db_path, table=table, filters=filters, limit=limit, schema=schema)


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
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO {schema}.flags (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by),
        )
        row_id = cursor.fetchone()["id"]
        conn.commit()
        return row_id
    finally:
        conn.close()


def update_flag_resolution(
    db_path: str,
    flag_id: int,
    resolved_by: str,
    note: Optional[str] = None,
    schema: str = None,
) -> bool:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE {schema}.flags SET resolved_at = CURRENT_TIMESTAMP, resolved_by = %s, resolution_note = %s
            WHERE id = %s AND resolved_at IS NULL
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
    if schema is None:
        schema = get_framework_schema()

    where = []
    params: list = []
    if only_unresolved:
        where.append("resolved_at IS NULL")
    if raw_data_id is not None:
        where.append("raw_data_id = %s")
        params.append(raw_data_id)
    if flag_type is not None:
        where.append("flag_type = %s")
        params.append(flag_type)

    sql = f"SELECT * FROM {schema}.flags"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY raised_at DESC LIMIT %s"
    params.append(limit)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return list(cursor.fetchall())
    finally:
        conn.close()


def get_already_cleaned_ids(db_path: str, schema: str = None) -> set[int]:
    if schema is None:
        schema = get_framework_schema()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT DISTINCT raw_data_id FROM {schema}.cleaned_data")
        return {int(row["raw_data_id"]) for row in cursor.fetchall()}
    finally:
        conn.close()


def insert_row(
    db_path: str,
    table: str,
    values: Dict,
    *,
    protected_fields: set[str] | None = None,
    schema: str = None,
) -> int:
    if schema is None:
        schema = get_framework_schema()

    schema_map = _get_schema_map(db_path, table)
    _validate_fields(db_path, table, values, protected_fields=protected_fields)
    columns = list(values)
    placeholders = ", ".join("%s" for _ in columns)
    returning = " RETURNING id" if "id" in schema_map else ""
    sql = f"INSERT INTO {schema}.{table} ({', '.join(columns)}) VALUES ({placeholders}){returning}"

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, [values[column] for column in columns])
        row_id = cursor.fetchone()["id"] if "id" in schema_map else 0
        conn.commit()
        return int(row_id)
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
    schema: str = None,
) -> bool:
    if schema is None:
        schema = get_framework_schema()

    schema_map = _get_schema_map(db_path, table)
    if id_column not in schema_map:
        raise ValueError(f"Unknown identifier column '{id_column}' for table '{table}'")
    _validate_fields(db_path, table, fields, protected_fields=(protected_fields or set()) | {id_column})

    set_clause = ", ".join(f"{col} = %s" for col in fields)
    values = list(fields.values()) + [record_id]

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE {schema}.{table} SET {set_clause} WHERE {id_column} = %s", values)
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
    schema: str = None,
) -> List[Dict]:
    if schema is None:
        schema = get_framework_schema()

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
            where_clauses.append(f"{col} = %s")
            params.append(val)

    sql = f"SELECT * FROM {schema}.{table}"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " LIMIT %s"
    params.append(limit)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return list(cursor.fetchall())
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
            WHERE table_name = %s
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
    schema_map = _get_schema_map(db_path, table_name)
    if column_name not in schema_map:
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (table_name, column_name) DO UPDATE SET
                inferred_role = EXCLUDED.inferred_role,
                role_confidence = EXCLUDED.role_confidence,
                normalizer = EXCLUDED.normalizer,
                validator = EXCLUDED.validator,
                is_sensitive = EXCLUDED.is_sensitive,
                notes = EXCLUDED.notes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                table_name,
                column_name,
                inferred_role,
                role_confidence,
                normalizer,
                validator,
                bool(is_sensitive),
                notes,
            ),
        )
        conn.commit()
    finally:
        conn.close()
