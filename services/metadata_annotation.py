"""LLM-driven column annotation service — populates column_metadata for any domain."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg import sql

from prompts.annotation import build_table_annotation_prompt
from seeders.registry import SeederRegistry

logger = logging.getLogger(__name__)


@dataclass
class AnnotationReport:
    domain: str
    annotated: int = 0
    skipped: int = 0
    low_confidence: list = field(default_factory=list)  # [{table_name, column_name, confidence}]
    failed: list = field(default_factory=list)  # [{table_name, column_name}] — LLM call failed, nothing persisted


class MetadataAnnotationService:
    LOW_CONFIDENCE_THRESHOLD = 0.70
    ANNOTATION_SYSTEM = "You are a database column annotator. Output JSON only."
    DESCRIPTION_MAX_CHARS = 300
    TABLE_ROW = "__table__"  # column_name used to store the table-level description

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
            todo = []
            for column in self._get_table_columns(table, conn, schema):
                if (table, column) in existing and not force:
                    report.skipped += 1
                    continue
                todo.append(column)
            if not todo:
                continue

            # One LLM call per table — sibling columns give the model context
            # a lone column name can't.
            result = self._annotate_table(
                domain, domain_description, table, todo, conn, schema
            )
            if result is None:
                # LLM call failed — do not persist junk rows that would
                # block re-annotation; record them so the caller can retry.
                report.failed.extend(
                    {"table_name": table, "column_name": c} for c in todo
                )
                continue

            table_desc = result.get("table_description")
            if table_desc and ((table, self.TABLE_ROW) not in existing or force):
                self._upsert_annotation(
                    domain, table, self.TABLE_ROW, table_desc, 0.9, conn, force,
                )

            for column in todo:
                col_result = result["columns"].get(column) or {
                    "description": column.replace("_", " "), "confidence": 0.3,
                }
                self._upsert_annotation(
                    domain, table, column,
                    col_result["description"], col_result["confidence"],
                    conn, force,
                )
                report.annotated += 1
                if col_result["confidence"] < self.LOW_CONFIDENCE_THRESHOLD:
                    report.low_confidence.append(
                        {"table_name": table, "column_name": column,
                         "confidence": col_result["confidence"]}
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

    def _annotate_table(
        self,
        domain: str,
        domain_description: str,
        table: str,
        columns: list[str],
        conn,
        schema: str = "public",
    ) -> Optional[dict]:
        """Annotate all given columns of a table in one LLM call.

        Returns {"table_description": str|None, "columns": {name: {description,
        confidence}}} — missing columns fall back at the call site. Returns
        None if the LLM call itself failed (nothing should be persisted)."""
        col_inputs = [
            {"name": c, "samples": self._get_sample_values(table, c, conn, schema=schema)}
            for c in columns
        ]
        prompt = build_table_annotation_prompt(
            domain, domain_description, table, col_inputs
        )

        try:
            resp = self._llm.messages_create(
                system=self.ANNOTATION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=max(1024, 200 * len(columns)),
            )
        except Exception as e:
            logger.error(f"Annotation LLM call failed for table {table}: {e}")
            return None

        out: dict = {"table_description": None, "columns": {}}
        try:
            text = next((b.text for b in resp.content if hasattr(b, "text")), "{}")
            data = json.loads(text.strip())
            desc = str(data.get("table_description") or "").strip()
            out["table_description"] = desc[: self.DESCRIPTION_MAX_CHARS] or None
            for item in data.get("columns", []):
                name = item.get("column_name")
                if name in columns:
                    out["columns"][name] = {
                        "description": str(item.get("description", ""))[: self.DESCRIPTION_MAX_CHARS],
                        "confidence": float(item.get("confidence", 0.5)),
                    }
        except Exception:
            logger.warning(f"Could not parse table annotation response for {table}")
        return out

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
