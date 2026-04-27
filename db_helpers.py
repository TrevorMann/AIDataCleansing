from typing import Optional, List, Dict
from database import get_db_connection


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
    imported_by: Optional[str] = None
) -> int:
    """Insert a raw data record. Returns the row ID."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO raw_data (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_raw_data_by_id(db_path: str, raw_data_id: int) -> Optional[Dict]:
    """Get a raw data record by ID."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM raw_data WHERE id = ?', (raw_data_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_raw_data(db_path: str) -> List[Dict]:
    """Get all raw data records."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM raw_data ORDER BY imported_at DESC')
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
    cleaned_by: Optional[str] = None
) -> int:
    """Insert a cleaned data record. Returns the row ID."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO cleaned_data (raw_data_id, name, age, city, address, postal_code, municipality, state_province, country, phone, validation_notes, cleaned_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (raw_data_id, name, age, city, address, postal_code, municipality, state_province, country, phone, validation_notes, cleaned_by))
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
    applied_by: Optional[str] = None
) -> int:
    """Insert an audit log entry. Returns the row ID."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO audit_log (raw_data_id, cleaned_data_id, rule_applied, description, applied_by)
        VALUES (?, ?, ?, ?, ?)
        ''', (raw_data_id, cleaned_data_id, rule_applied, description, applied_by))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_audit_log_for_record(db_path: str, raw_data_id: int) -> List[Dict]:
    """Get all audit log entries for a raw data record."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM audit_log WHERE raw_data_id = ? ORDER BY applied_at', (raw_data_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_raw_data(db_path: str, record_id: int, fields: Dict) -> bool:
    """Update specific fields on a raw_data record. Returns True if a row was updated."""
    PROTECTED = {'id', 'imported_at'}
    bad = set(fields.keys()) & PROTECTED
    if bad:
        raise ValueError(f"Cannot update protected fields: {', '.join(sorted(bad))}")
    if not fields:
        raise ValueError("No fields specified for update")

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [record_id]

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE raw_data SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_cleaned_data(db_path: str, record_id: int, fields: Dict) -> bool:
    """Update specific fields on a cleaned_data record. Returns True if a row was updated."""
    PROTECTED = {'id', 'raw_data_id', 'cleaned_at'}
    bad = set(fields.keys()) & PROTECTED
    if bad:
        raise ValueError(f"Cannot update protected fields: {', '.join(sorted(bad))}")
    if not fields:
        raise ValueError("No fields specified for update")

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [record_id]

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE cleaned_data SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_raw_data(db_path: str, record_id: int) -> bool:
    """Delete a raw_data record by ID. Returns True if deleted."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM raw_data WHERE id = ?", (record_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_cleaned_data_for_raw(db_path: str, raw_data_id: int) -> List[Dict]:
    """Return all cleaned_data records for a given raw_data_id."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cleaned_data WHERE raw_data_id = ?", (raw_data_id,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def query_records(
    db_path: str,
    table: str = 'raw_data',
    filters: Optional[Dict] = None,
    limit: int = 50,
) -> List[Dict]:
    """Filter records from a table. All filter conditions are ANDed."""
    VALID_TABLES = {'raw_data', 'cleaned_data', 'audit_log'}
    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table '{table}'. Must be one of: {', '.join(sorted(VALID_TABLES))}")

    where_clauses = []
    params = []
    if filters:
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


def insert_flag(
    db_path: str,
    raw_data_id: int,
    flag_type: str,
    severity: str,
    reason: str,
    raised_by: str,
    cleaned_data_id: Optional[int] = None,
) -> int:
    """Insert a flag. Returns the row ID."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO flags (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (raw_data_id, cleaned_data_id, flag_type, severity, reason, raised_by))
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
    """Mark a flag as resolved. Returns True if a row was updated."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute('''
        UPDATE flags SET resolved_at = CURRENT_TIMESTAMP, resolved_by = ?, resolution_note = ?
        WHERE id = ? AND resolved_at IS NULL
        ''', (resolved_by, note, flag_id))
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
) -> List[Dict]:
    """Query flags. Defaults to unresolved only."""
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
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
