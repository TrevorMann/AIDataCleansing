from typing import List, Dict
from database import get_db_connection


def get_table_schema(db_path: str, table_name: str) -> List[Dict]:
    """Get column information for a table.

    Returns list of dicts with keys: name, type, notnull, default_value, pk
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()

        schema = []
        for row in rows:
            schema.append({
                'name': row[1],
                'type': row[2],
                'notnull': bool(row[3]),
                'default_value': row[4],
                'pk': bool(row[5])
            })

        return schema
    finally:
        conn.close()


def get_all_schemas(db_path: str) -> Dict[str, List[Dict]]:
    """Get schemas for all tables in the database."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

    schemas = {}
    for table in tables:
        schemas[table] = get_table_schema(db_path, table)

    return schemas


def format_schema_for_prompt(db_path: str) -> str:
    """Format all schemas as a string for inclusion in Claude's prompt context."""
    schemas = get_all_schemas(db_path)

    formatted = "<DATABASE_SCHEMA>\n"

    for table_name, columns in schemas.items():
        formatted += f"\n{table_name}:\n"
        for col in columns:
            pk_marker = " [PRIMARY KEY]" if col['pk'] else ""
            notnull_marker = " [NOT NULL]" if col['notnull'] else ""
            formatted += f"  - {col['name']} ({col['type']}){pk_marker}{notnull_marker}\n"

    formatted += "\n</DATABASE_SCHEMA>"
    return formatted


def get_table_columns(db_path: str, table_name: str) -> List[str]:
    """Get list of column names for a table."""
    schema = get_table_schema(db_path, table_name)
    return [col['name'] for col in schema]


def get_column_metadata(db_path: str, table_name: str) -> Dict[str, str]:
    """Return {column_name: description} for a table from the column_metadata table."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT column_name, description FROM column_metadata WHERE table_name = ?',
            (table_name,)
        )
        return {row['column_name']: row['description'] for row in cursor.fetchall() if row['description']}
    except Exception:
        return {}
    finally:
        conn.close()
