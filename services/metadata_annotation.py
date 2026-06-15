"""LLM-driven column annotation service — populates column_metadata for any domain."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg import sql

from prompts.annotation import build_annotation_prompt
from seeders.registry import SeederRegistry

logger = logging.getLogger(__name__)


@dataclass
class AnnotationReport:
    domain: str
    annotated: int = 0
    skipped: int = 0
    low_confidence: list = field(default_factory=list)  # [{table_name, column_name, confidence}]


class MetadataAnnotationService:
    LOW_CONFIDENCE_THRESHOLD = 0.70
    ANNOTATION_SYSTEM = "You are a database column annotator. Output JSON only."

    def __init__(self, llm_client: Optional[Any] = None):
        self._llm = llm_client

    # ── Public API ──────────────────────────────────────────────────────────

    def list_gaps(self, domain: str, conn, tables: list[str], schema: str = "public") -> list[dict]:
        """Return [{table_name, column_name}] lacking annotation for domain.

        tables must be provided explicitly — use DomainInitializer(domain).get_registered_tables().
        """
        existing = self._get_existing_annotations(domain, conn)
        gaps = []
        for table in tables:
            for col in self._get_table_columns(table, conn, schema):
                if (table, col) not in existing:
                    gaps.append({"table_name": table, "column_name": col})
        return gaps

    def run(
        self,
        domain: str,
        conn,
        tables: list[str],
        schema: str = "public",
        force: bool = False,
    ) -> AnnotationReport:
        """Annotate unannotated columns for domain. Skips existing unless force=True.

        tables must be provided explicitly — use DomainInitializer(domain).get_registered_tables().
        """
        if self._llm is None:
            raise ValueError(
                "llm_client is required to annotate; use list_gaps() for dry-run discovery."
            )

        # Verify the metadata table exists
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema='data_details' AND table_name='column_metadata'"
                )
                if not cur.fetchone():
                    raise ValueError("Table data_details.column_metadata does not exist. Run db/pg_init.py first.")
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to verify metadata table: {e}")
            raise

        try:
            sr = SeederRegistry(domain)
            domain_description = sr.manifest.get("description", domain)
        except FileNotFoundError:
            domain_description = domain

        existing = self._get_existing_annotations(domain, conn)
        conn.commit()  # Commit the SELECT so next transaction is clean

        report = AnnotationReport(domain=domain)

        for table in tables:
            for column in self._get_table_columns(table, conn, schema):
                if (table, column) in existing and not force:
                    report.skipped += 1
                    continue

                result = self._annotate_column(
                    domain, domain_description, table, column, conn, schema
                )
                self._upsert_annotation(
                    domain, table, column,
                    result["description"], result["confidence"],
                    conn, force,
                )
                report.annotated += 1
                if result["confidence"] < self.LOW_CONFIDENCE_THRESHOLD:
                    report.low_confidence.append(
                        {"table_name": table, "column_name": column,
                         "confidence": result["confidence"]}
                    )

        return report

    # ── Private helpers ─────────────────────────────────────────────────────

    def _get_existing_annotations(self, domain: str, conn) -> set:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name, column_name FROM data_details.column_metadata WHERE domain = %s",
                    (domain,),
                )
                return {(row["table_name"], row["column_name"]) for row in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Could not fetch existing annotations: {e}. Starting fresh.")
            conn.rollback()
            return set()

    def _get_table_columns(self, table: str, conn, schema: str = "public") -> list[str]:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                    (schema, table),
                )
                return [row["column_name"] for row in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Could not fetch columns for {table}.{schema}: {e}")
            conn.rollback()
            return []
        finally:
            conn.commit()

    def _get_sample_values(self, table: str, column: str, conn, schema: str = "public", n: int = 5) -> list:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {col} FROM {schema}.{t} WHERE {col} IS NOT NULL LIMIT %s").format(
                        schema=sql.Identifier(schema), t=sql.Identifier(table), col=sql.Identifier(column)
                    ),
                    (n,),
                )
                # Cursor returns dicts, so access the column value by name
                return [row[column] for row in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Could not sample values for {schema}.{table}.{column}: {e}")
            conn.rollback()
            return []
        finally:
            conn.commit()

    def _annotate_column(
        self,
        domain: str,
        domain_description: str,
        table: str,
        column: str,
        conn,
        schema: str = "public",
    ) -> dict:
        samples = self._get_sample_values(table, column, conn, schema=schema)
        prompt = build_annotation_prompt(domain, domain_description, table, column, samples)

        try:
            resp = self._llm.messages_create(
                system=self.ANNOTATION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=256,
            )
            text = next((b.text for b in resp.content if hasattr(b, "text")), "{}")
            result = json.loads(text.strip())
            return {
                "description": str(result.get("description", ""))[:120],
                "confidence": float(result.get("confidence", 0.5)),
            }
        except Exception:
            return {"description": column.replace("_", " "), "confidence": 0.3}

    def _upsert_annotation(
        self,
        domain: str,
        table: str,
        column: str,
        description: str,
        confidence: float,
        conn,
        force: bool,
    ) -> None:
        now = datetime.now(timezone.utc)
        try:
            with conn.cursor() as cur:
                if force:
                    cur.execute(
                        """
                        INSERT INTO data_details.column_metadata
                            (domain, table_name, column_name, description,
                             is_llm_generated, confidence, generated_at)
                        VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                        ON CONFLICT (domain, table_name, column_name) DO UPDATE
                          SET description      = EXCLUDED.description,
                              is_llm_generated = TRUE,
                              confidence       = EXCLUDED.confidence,
                              generated_at     = EXCLUDED.generated_at
                        """,
                        (domain, table, column, description, confidence, now),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO data_details.column_metadata
                            (domain, table_name, column_name, description,
                             is_llm_generated, confidence, generated_at)
                        VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                        ON CONFLICT (domain, table_name, column_name) DO NOTHING
                        """,
                        (domain, table, column, description, confidence, now),
                    )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to upsert annotation for {domain}.{table}.{column}: {e}")
            raise
