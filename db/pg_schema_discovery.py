from typing import Dict, List

from db.pg_init import get_db_connection


def get_table_schema(db_path: str, table_name: str) -> List[Dict]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                c.column_name AS name,
                c.data_type AS type,
                c.is_nullable = 'NO' AS notnull,
                c.column_default AS default_value,
                EXISTS (
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = c.table_schema
                      AND tc.table_name = c.table_name
                      AND kcu.column_name = c.column_name
                ) AS pk
            FROM information_schema.columns c
            WHERE c.table_schema = 'public' AND c.table_name = %s
            ORDER BY c.ordinal_position
            """,
            (table_name,),
        )
        return list(cursor.fetchall())
    finally:
        conn.close()


def get_all_schemas(db_path: str) -> Dict[str, List[Dict]]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        tables = [row["table_name"] for row in cursor.fetchall()]
    finally:
        conn.close()
    return {table: get_table_schema(db_path, table) for table in tables}


def format_schema_for_prompt(db_path: str) -> str:
    schemas = get_all_schemas(db_path)
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


def get_table_columns(db_path: str, table_name: str) -> List[str]:
    return [col["name"] for col in get_table_schema(db_path, table_name)]


def get_column_metadata(db_path: str, table_name: str) -> Dict[str, str]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT column_name, description FROM column_metadata WHERE table_name = %s",
            (table_name,),
        )
        return {row["column_name"]: row["description"] for row in cursor.fetchall() if row["description"]}
    except Exception:
        return {}
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
            WHERE p.table_name = %s
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


def get_all_column_profiles(db_path: str) -> Dict[str, Dict[str, Dict]]:
    schemas = get_all_schemas(db_path)
    return {table_name: get_column_profiles(db_path, table_name) for table_name in schemas}
