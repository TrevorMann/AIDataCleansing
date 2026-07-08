"""Query pattern memory helpers — track which search queries work per domain/gap."""

import json
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from db.schema_config import get_framework_schema


def top_queries_for(conn, domain: str, gap_type: str, k: int = 3, schema: str = None) -> List[str]:
    """Return top-k query templates for gap, ordered by success rate.

    Falls back to seed queries (success_count=0, failure_count=0) when no
    learned signal exists yet.
    """
    if schema is None:
        schema = get_framework_schema()

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT query_template
            FROM {schema}.query_pattern_memory
            WHERE domain = %s AND gap_type = %s
            ORDER BY
                (success_count::float / NULLIF(success_count + failure_count, 0)) DESC NULLS LAST,
                success_count DESC
            LIMIT %s
            """,
            (domain, gap_type, k),
        )
        return [row[0] for row in cur.fetchall()]


def gap_detection_for(conn, domain: str, schema: str = None) -> dict:
    """Return {column_name: gap_detection_dict} for a domain using a live conn.

    Mirrors top_queries_for: postgres-first, best-effort. Returns {} on ANY DB
    error (missing table, bad schema, etc.) — intentional: the classifier then
    sees no config and emits no gaps, degrading gracefully rather than crashing.
    """
    if schema is None:
        schema = get_framework_schema()
    out = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT column_name, gap_detection FROM {schema}.column_metadata "
                f"WHERE domain = %s AND gap_detection IS NOT NULL",
                (domain,),
            )
            for row in cur.fetchall():
                column_name, cfg = row[0], row[1]
                if isinstance(cfg, str):
                    cfg = json.loads(cfg)
                if cfg:
                    out[column_name] = cfg
    except Exception:
        return {}  # best-effort: classifier falls back to empty config
    return out


def record_query_outcome(conn, domain: str, gap_type: str, query_template: str, success: bool, schema: str = None):
    """Increment success or failure counter for a query template."""
    if schema is None:
        schema = get_framework_schema()

    col = "success_count" if success else "failure_count"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {schema}.query_pattern_memory (domain, gap_type, query_template, {col}, last_used_at)
            VALUES (%s, %s, %s, 1, NOW())
            ON CONFLICT (domain, gap_type, query_template) DO UPDATE
                SET {col} = {schema}.query_pattern_memory.{col} + 1,
                    last_used_at = NOW()
            """,
            (domain, gap_type, query_template),
        )
    conn.commit()


def update_source_score(conn, domain_key: str, url_host: str, success: bool, schema: str = None):
    """Adjust trust score for a source host based on parse success."""
    if schema is None:
        schema = get_framework_schema()

    col = "success_count" if success else "failure_count"
    delta = 0.02 if success else -0.01
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {schema}.source_registry (domain_key, url_host, {col}, trust_score)
            VALUES (%s, %s, 1, 0.5 + %s)
            ON CONFLICT (domain_key, url_host) DO UPDATE
                SET {col} = {schema}.source_registry.{col} + 1,
                    trust_score = GREATEST(0.0, LEAST(1.0, {schema}.source_registry.trust_score + %s))
            """,
            (domain_key, url_host, delta, delta),
        )
    conn.commit()


def load_query_packs(conn, domain: str, packs_yaml_path: str, schema: str = None):
    """Seed query_pattern_memory from a query packs YAML file. Idempotent."""
    if schema is None:
        schema = get_framework_schema()

    import yaml
    from pathlib import Path

    path = Path(packs_yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Query packs file not found: {path}")

    with open(path) as f:
        packs = yaml.safe_load(f)

    inserted = 0
    for gap_type, spec in packs.get("gap_types", {}).items():
        for query_template in spec.get("seed_queries", []):
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {schema}.query_pattern_memory (domain, gap_type, query_template)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (domain, gap_type, query_template) DO NOTHING
                    """,
                    (domain, gap_type, query_template),
                )
                inserted += 1

    # Seed source_registry with trusted sources
    for host in packs.get("trusted_sources", []):
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {schema}.source_registry (domain_key, url_host, trust_score)
                VALUES (%s, %s, 0.8)
                ON CONFLICT (domain_key, url_host) DO NOTHING
                """,
                (domain, host),
            )

    conn.commit()
    return inserted
