import json
import os
import urllib.request
from typing import Any


_EMBEDDING_MODEL = "openai/text-embedding-3-small"
_EMBEDDING_DIMS = 1536


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.12g}" for value in values) + "]"


def init_vector_tables(conn: Any) -> None:
    cursor = conn.cursor()
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS search_cache (
            id              SERIAL PRIMARY KEY,
            query_text      TEXT NOT NULL,
            query_embedding vector(1536) NOT NULL,
            result_text     TEXT NOT NULL,
            cached_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hit_count       INTEGER DEFAULT 0
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_search_cache_embedding
            ON search_cache USING ivfflat (query_embedding vector_cosine_ops)
            WITH (lists = 100)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS address_lookup (
            id                SERIAL PRIMARY KEY,
            address_text      TEXT NOT NULL,
            postal_code       TEXT,
            municipality      TEXT,
            city              TEXT,
            state_province    TEXT,
            country           TEXT,
            address_embedding vector(1536) NOT NULL,
            source            TEXT,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_address_lookup_embedding
            ON address_lookup USING ivfflat (address_embedding vector_cosine_ops)
            WITH (lists = 100)
        """
    )


def embed(text: str) -> list[float]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment.")

    payload = json.dumps({"model": _EMBEDDING_MODEL, "input": text}).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    items = data.get("data") or []
    if not items:
        raise ValueError("Embedding response was empty")

    vector = items[0].get("embedding")
    if not isinstance(vector, list) or len(vector) != _EMBEDDING_DIMS:
        raise ValueError(
            f"Expected {_EMBEDDING_DIMS}-dim embedding, got {len(vector) if isinstance(vector, list) else 'invalid'}"
        )
    return vector


def search_cache_lookup(conn: Any, query: str, threshold: float = 0.85) -> str | None:
    query_embedding = embed(query)
    max_distance = 1 - threshold
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, result_text
        FROM search_cache
        WHERE query_embedding <=> %s::vector < %s
        ORDER BY query_embedding <=> %s::vector
        LIMIT 1
        """,
        (_vector_literal(query_embedding), max_distance, _vector_literal(query_embedding)),
    )
    row = cursor.fetchone()
    if not row:
        return None

    cursor.execute("UPDATE search_cache SET hit_count = hit_count + 1 WHERE id = %s", (row["id"],))
    conn.commit()
    return row["result_text"]


def search_cache_store(conn: Any, query: str, result: str) -> None:
    query_embedding = embed(query)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO search_cache (query_text, query_embedding, result_text)
        VALUES (%s, %s::vector, %s)
        """,
        (query, _vector_literal(query_embedding), result),
    )
    conn.commit()


def address_lookup(conn: Any, text: str, top_k: int = 3) -> list[dict]:
    query_embedding = embed(text)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT address_text, postal_code, municipality, city, state_province, country, source
        FROM address_lookup
        ORDER BY address_embedding <=> %s::vector
        LIMIT %s
        """,
        (_vector_literal(query_embedding), top_k),
    )
    return list(cursor.fetchall())


def address_upsert(conn: Any, canonical: dict) -> None:
    address_text = canonical.get("address_text") or canonical.get("address") or ""
    if not address_text:
        raise ValueError("canonical address data must include address_text or address")

    vector = embed(
        " | ".join(
            str(canonical.get(key, "") or "")
            for key in (
                "address_text",
                "address",
                "postal_code",
                "municipality",
                "city",
                "state_province",
                "country",
            )
        )
    )
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id
        FROM address_lookup
        WHERE address_text = %s
          AND postal_code IS NOT DISTINCT FROM %s
          AND municipality IS NOT DISTINCT FROM %s
          AND city IS NOT DISTINCT FROM %s
          AND state_province IS NOT DISTINCT FROM %s
          AND country IS NOT DISTINCT FROM %s
        LIMIT 1
        """,
        (
            address_text,
            canonical.get("postal_code"),
            canonical.get("municipality"),
            canonical.get("city"),
            canonical.get("state_province"),
            canonical.get("country"),
        ),
    )
    row = cursor.fetchone()
    params = (
        address_text,
        canonical.get("postal_code"),
        canonical.get("municipality"),
        canonical.get("city"),
        canonical.get("state_province"),
        canonical.get("country"),
        _vector_literal(vector),
        canonical.get("source"),
    )
    if row:
        cursor.execute(
            """
            UPDATE address_lookup
            SET address_text = %s,
                postal_code = %s,
                municipality = %s,
                city = %s,
                state_province = %s,
                country = %s,
                address_embedding = %s::vector,
                source = %s
            WHERE id = %s
            """,
            params + (row["id"],),
        )
    else:
        cursor.execute(
            """
            INSERT INTO address_lookup (
                address_text,
                postal_code,
                municipality,
                city,
                state_province,
                country,
                address_embedding,
                source
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)
            """,
            params,
        )
    conn.commit()
