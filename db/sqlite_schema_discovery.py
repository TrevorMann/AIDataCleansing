from typing import Dict, List

from db.sqlite_init import get_db_connection


def get_table_schema(db_path: str, table_name: str, schema: str = "main") -> List[Dict]:
    """Get table schema. Schema parameter ignored for SQLite (only has 'main')."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
        return [
            {
                "name": row[1],
                "type": row[2],
                "notnull": bool(row[3]),
                "default_value": row[4],
                "pk": bool(row[5]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_all_schemas(db_path: str, schema: str = "main") -> Dict[str, List[Dict]]:
    """Get all tables. Schema parameter ignored for SQLite (only has 'main')."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()
    return {table: get_table_schema(db_path, table, schema) for table in tables}


def get_available_schemas(db_path: str) -> List[str]:
    """SQLite only has 'main' schema."""
    return ["main"]


def format_schema_for_prompt(db_path: str, schema: str = "main") -> str:
    """Format schema for prompt. Schema parameter ignored for SQLite."""
    schemas = get_all_schemas(db_path, schema)
    profiles = get_all_column_profiles(db_path)
    formatted = "<DATABASE_SCHEMA>\n"
    for table_name, columns in schemas.items():
        formatted += f"\n{table_name}:\n"
        for col in columns:
            pk_marker = " [PRIMARY KEY]" if col["pk"] else ""
            notnull_marker = " [NOT NULL]" if col["notnull"] else ""
            profile = profiles.get(table_name, {}).get(col["name"], {})
            role_marker = f" [ROLE: {profile['inferred_role']}]" if profile.get("inferred_role") else ""
            description = profile.get("description") or profile.get("notes")
            description_marker = f" - {description}" if description else ""
            formatted += (
                f"  - {col['name']} ({col['type']}){pk_marker}{notnull_marker}{role_marker}{description_marker}\n"
            )
    formatted += "\n</DATABASE_SCHEMA>"
    return formatted


def get_table_columns(db_path: str, table_name: str, schema: str = "main") -> List[str]:
    """Get table columns. Schema parameter ignored for SQLite."""
    return [col["name"] for col in get_table_schema(db_path, table_name, schema)]


def get_column_metadata(db_path: str, table_name: str) -> Dict[str, str]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT column_name, description FROM column_metadata WHERE table_name = ?",
            (table_name,),
        )
        return {row["column_name"]: row["description"] for row in cursor.fetchall() if row["description"]}
    except Exception:
        return {}
    finally:
        conn.close()


def get_gap_detection(db_path: str, domain: str) -> dict:
    """Return {column_name: gap_detection_dict} for a domain, across its tables."""
    import json
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT column_name, gap_detection FROM column_metadata "
            "WHERE domain = ? AND gap_detection IS NOT NULL",
            (domain,),
        )
        out = {}
        for row in cursor.fetchall():
            raw = row["gap_detection"]
            if raw:
                out[row["column_name"]] = json.loads(raw)
        return out
    except Exception:
        return {}  # best-effort: classifier falls back to empty config on any DB error
    finally:
        conn.close()


def get_column_profiles(db_path: str, table_name: str) -> Dict[str, Dict]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                p.column_name,
                p.inferred_role,
                p.role_confidence,
                p.normalizer,
                p.validator,
                p.is_sensitive,
                p.notes,
                m.description
            FROM column_profiles p
            LEFT JOIN column_metadata m
              ON m.table_name = p.table_name AND m.column_name = p.column_name
            WHERE p.table_name = ?
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
                "description": row["description"],
            }
            for row in cursor.fetchall()
        }
    finally:
        conn.close()


def get_all_column_profiles(db_path: str, schema: str = "main") -> Dict[str, Dict[str, Dict]]:
    """Get all column profiles. Schema parameter ignored for SQLite."""
    schemas = get_all_schemas(db_path, schema)
    return {table_name: get_column_profiles(db_path, table_name) for table_name in schemas}
