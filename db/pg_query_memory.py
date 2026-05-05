"""Query pattern memory helpers — track which search queries work per domain/gap."""

import json
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse


def top_queries_for(conn, domain: str, gap_type: str, k: int = 3) -> List[str]:
    """Return top-k query templates for gap, ordered by success rate.

    Falls back to seed queries (success_count=0, failure_count=0) when no
    learned signal exists yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT query_template
            FROM query_pattern_memory
            WHERE domain = %s AND gap_type = %s
            ORDER BY
                (success_count::float / NULLIF(success_count + failure_count, 0)) DESC NULLS LAST,
                success_count DESC
            LIMIT %s
            """,
            (domain, gap_type, k),
        )
        return [row[0] for row in cur.fetchall()]


def record_query_outcome(conn, domain: str, gap_type: str, query_template: str, success: bool):
    """Increment success or failure counter for a query template."""
    col = "success_count" if success else "failure_count"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO query_pattern_memory (domain, gap_type, query_template, {col}, last_used_at)
            VALUES (%s, %s, %s, 1, NOW())
            ON CONFLICT (domain, gap_type, query_template) DO UPDATE
                SET {col} = query_pattern_memory.{col} + 1,
                    last_used_at = NOW()
            """,
            (domain, gap_type, query_template),
        )
    conn.commit()


def update_source_score(conn, domain_key: str, url_host: str, success: bool):
    """Adjust trust score for a source host based on parse success."""
    col = "success_count" if success else "failure_count"
    delta = 0.02 if success else -0.01
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO source_registry (domain_key, url_host, {col}, trust_score)
            VALUES (%s, %s, 1, 0.5 + %s)
            ON CONFLICT (domain_key, url_host) DO UPDATE
                SET {col} = source_registry.{col} + 1,
                    trust_score = GREATEST(0.0, LEAST(1.0, source_registry.trust_score + %s))
            """,
            (domain_key, url_host, delta, delta),
        )
    conn.commit()


def load_query_packs(conn, domain: str, packs_yaml_path: str):
    """Seed query_pattern_memory from a query packs YAML file. Idempotent."""
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
                    """
                    INSERT INTO query_pattern_memory (domain, gap_type, query_template)
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
                """
                INSERT INTO source_registry (domain_key, url_host, trust_score)
                VALUES (%s, %s, 0.8)
                ON CONFLICT (domain_key, url_host) DO NOTHING
                """,
                (domain, host),
            )

    conn.commit()
    return inserted
