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


def format_schema_for_prompt(db_path: str, domain: str = "base") -> str:
    """
    Build schema string for LLM prompt using column_metadata as the source of truth.

    Only tables/columns with entries in column_metadata appear — prevents internal
    infrastructure tables (spell_corrections, plan_cache, etc.) from leaking into prompt.

    Domain entries override 'base' entries for the same (table, column).
    Pass domain='base' for generic base prompt; domain='real_estate' etc. for domain-specific.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        domains_to_query = ["base"] if domain == "base" else ["base", domain]
        placeholders = ",".join("?" * len(domains_to_query))
        cursor.execute(
            f"SELECT domain, table_name, column_name, description "
            f"FROM column_metadata WHERE domain IN ({placeholders}) "
            f"ORDER BY table_name, column_name",
            domains_to_query,
        )
        rows = cursor.fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    if not rows:
        return "<DATABASE_SCHEMA>\n(No schema metadata available for this domain)\n</DATABASE_SCHEMA>"

    # Build merged dict — base first, then domain overrides
    tables: Dict[str, Dict[str, str]] = {}
    for priority in ["base", domain]:
        for row in rows:
            if row["domain"] == priority:
                tables.setdefault(row["table_name"], {})[row["column_name"]] = row["description"] or ""

    # Fetch column types from PRAGMA for each table that has metadata
    type_map: Dict[str, Dict[str, str]] = {}
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        for table_name in tables:
            try:
                cursor.execute(f"PRAGMA table_info({table_name})")
                type_map[table_name] = {r["name"]: r["type"] for r in cursor.fetchall()}
            except Exception:
                type_map[table_name] = {}
    finally:
        conn.close()

    formatted = "<DATABASE_SCHEMA>\n"
    for table_name in sorted(tables):
        formatted += f"\n{table_name}:\n"
        col_types = type_map.get(table_name, {})
        for col_name, description in sorted(tables[table_name].items()):
            col_type = col_types.get(col_name, "TEXT")
            desc_str = f" — {description}" if description else ""
            formatted += f"  - {col_name} ({col_type}){desc_str}\n"
    formatted += "\n</DATABASE_SCHEMA>"
    return formatted


def get_table_columns(db_path: str, table_name: str) -> List[str]:
    """Get list of column names for a table."""
    schema = get_table_schema(db_path, table_name)
    return [col['name'] for col in schema]


def get_column_metadata(db_path: str, table_name: str, domain: str = "base") -> Dict[str, str]:
    """Return {column_name: description} for a table, merging base + domain entries."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        domains_to_query = ["base"] if domain == "base" else ["base", domain]
        placeholders = ",".join("?" * len(domains_to_query))
        cursor.execute(
            f"SELECT domain, column_name, description FROM column_metadata "
            f"WHERE table_name = ? AND domain IN ({placeholders})",
            [table_name] + domains_to_query,
        )
        result: Dict[str, str] = {}
        for priority in ["base", domain]:
            for row in cursor.fetchall():
                if row["domain"] == priority and row["description"]:
                    result[row["column_name"]] = row["description"]
        return result
    except Exception:
        return {}
    finally:
        conn.close()
