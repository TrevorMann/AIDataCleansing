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
    DEFAULT_TABLES = ["raw_data", "cleaned_data"]
    LOW_CONFIDENCE_THRESHOLD = 0.70
    ANNOTATION_SYSTEM = "You are a database column annotator. Output JSON only."

    def __init__(self, llm_client: Optional[Any] = None):
        self._llm = llm_client

    # ── Public API ──────────────────────────────────────────────────────────

    def list_gaps(self, domain: str, conn, tables: list[str] = None) -> list[dict]:
        """Return [{table_name, column_name}] lacking annotation for domain."""
        tables = tables or self.DEFAULT_TABLES
        existing = self._get_existing_annotations(domain, conn)
        gaps = []
        for table in tables:
            for col in self._get_table_columns(table, conn):
                if (table, col) not in existing:
                    gaps.append({"table_name": table, "column_name": col})
        return gaps

    def run(
        self,
        domain: str,
        conn,
        force: bool = False,
        tables: list[str] = None,
    ) -> AnnotationReport:
        """Annotate unannotated columns for domain. Skips existing unless force=True."""
        if self._llm is None:
            raise ValueError(
                "llm_client is required to annotate; use list_gaps() for dry-run discovery."
            )
        tables = tables or self.DEFAULT_TABLES
        try:
            sr = SeederRegistry(domain)
            domain_description = sr.manifest.get("description", domain)
        except FileNotFoundError:
            domain_description = domain

        existing = self._get_existing_annotations(domain, conn)
        report = AnnotationReport(domain=domain)

        for table in tables:
            for column in self._get_table_columns(table, conn):
                if (table, column) in existing and not force:
                    report.skipped += 1
                    continue

                result = self._annotate_column(
                    domain, domain_description, table, column, conn
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
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM column_metadata WHERE domain = %s",
                (domain,),
            )
            return {(row[0], row[1]) for row in cur.fetchall()}

    def _get_table_columns(self, table: str, conn) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            return [row[0] for row in cur.fetchall()]

    def _get_sample_values(self, table: str, column: str, conn, n: int = 5) -> list:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE {} IS NOT NULL LIMIT %s").format(
                        sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
                    ),
                    (n,),
                )
                return [row[0] for row in cur.fetchall()]
        except Exception:
            return []

    def _annotate_column(
        self,
        domain: str,
        domain_description: str,
        table: str,
        column: str,
        conn,
    ) -> dict:
        samples = self._get_sample_values(table, column, conn)
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
        with conn.cursor() as cur:
            if force:
                cur.execute(
                    """
                    INSERT INTO column_metadata
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
                    INSERT INTO column_metadata
                        (domain, table_name, column_name, description,
                         is_llm_generated, confidence, generated_at)
                    VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                    ON CONFLICT (domain, table_name, column_name) DO NOTHING
                    """,
                    (domain, table, column, description, confidence, now),
                )
        conn.commit()
