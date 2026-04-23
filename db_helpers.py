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
    cursor = conn.cursor()

    cursor.execute('''
    INSERT INTO raw_data (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (name, age, city, address, postal_code, municipality, state_province, country, phone, imported_by))

    conn.commit()
    row_id = cursor.lastrowid
    conn.close()

    return row_id


def get_raw_data_by_id(db_path: str, raw_data_id: int) -> Optional[Dict]:
    """Get a raw data record by ID."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM raw_data WHERE id = ?', (raw_data_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_all_raw_data(db_path: str) -> List[Dict]:
    """Get all raw data records."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM raw_data ORDER BY imported_at DESC')
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


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
    cursor = conn.cursor()

    cursor.execute('''
    INSERT INTO cleaned_data (raw_data_id, name, age, city, address, postal_code, municipality, state_province, country, phone, validation_notes, cleaned_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (raw_data_id, name, age, city, address, postal_code, municipality, state_province, country, phone, validation_notes, cleaned_by))

    conn.commit()
    row_id = cursor.lastrowid
    conn.close()

    return row_id


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
    cursor = conn.cursor()

    cursor.execute('''
    INSERT INTO audit_log (raw_data_id, cleaned_data_id, rule_applied, description, applied_by)
    VALUES (?, ?, ?, ?, ?)
    ''', (raw_data_id, cleaned_data_id, rule_applied, description, applied_by))

    conn.commit()
    row_id = cursor.lastrowid
    conn.close()

    return row_id


def get_audit_log_for_record(db_path: str, raw_data_id: int) -> List[Dict]:
    """Get all audit log entries for a raw data record."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM audit_log WHERE raw_data_id = ? ORDER BY applied_at', (raw_data_id,))
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]
