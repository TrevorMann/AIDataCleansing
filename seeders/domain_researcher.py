"""LLM-driven domain researcher.

Asks structured questions about a domain, then calls the LLM to generate:
  - spell_corrections.csv     — domain-specific misspellings
  - query_packs.yaml          — web search templates per gap type
  - column_metadata.yaml      — column descriptions and data types

The interactive CLI lives in scripts/research_domain.py.
This module contains all testable logic.
"""

import csv
import json
import logging
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


def _log_usage(model: str, usage: Any) -> None:
    """Log token usage for cost tracking (every messages.create call)."""
    if usage is None:
        return
    logger.info(
        "[%s] input=%s output=%s",
        model,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
    )


# ── data structures ───────────────────────────────────────────────────────────────

@dataclass
class Question:
    key: str
    prompt: str
    hint: str = ""     # shown below the prompt as a small example hint


@dataclass
class SpellCorrection:
    wrong: str
    right: str
    source: str = "llm_generated"
    confidence: float = 0.90


@dataclass
class QueryPack:
    gap_type: str
    seed_queries: List[str]


@dataclass
class ColumnDescription:
    column_name: str
    description: str
    example_values: List[str]
    data_type: str   # text | date | phone | email | numeric | code


@dataclass
class ResearchBundle:
    domain: str
    spell_corrections: List[SpellCorrection] = field(default_factory=list)
    query_packs: List[QueryPack] = field(default_factory=list)
    column_descriptions: List[ColumnDescription] = field(default_factory=list)


# ── questionnaire ─────────────────────────────────────────────────────────────────

_QUESTIONS = [
    Question(
        key="entity_description",
        prompt="What kind of records does this domain clean?",
        hint="e.g. sports event tickets, patient records, hotel bookings, job applications",
    ),
    Question(
        key="fields",
        prompt="What are the main data fields in a record? (comma-separated)",
        hint="e.g. name, email, venue_name, event_date, ticket_type, price",
    ),
    Question(
        key="text_fields",
        prompt="Which fields contain free-text that may have spelling or capitalization errors?",
        hint="e.g. venue_name, team_name, city, description — these feed the spell-correction seeder",
    ),
    Question(
        key="linking_fields",
        prompt="What fields uniquely identify a record, or help detect near-duplicates?",
        hint="e.g. event_id  —or—  venue_name + home_team + event_date together",
    ),
    Question(
        key="gap_types",
        prompt="What data quality gaps typically require a web search to resolve?",
        hint="e.g. missing venue address, unknown team abbreviation, ambiguous event date",
    ),
    Question(
        key="trusted_sources",
        prompt="Which authoritative websites should be searched for this domain? (comma-separated)",
        hint="e.g. espn.com, ticketmaster.com, seatgeek.com, wikipedia.org",
    ),
    Question(
        key="industry_context",
        prompt="Any additional domain-specific context the LLM should know?",
        hint="e.g. team names use 3-letter codes, venues have multiple common name variants — leave blank to skip",
    ),
]

# ── schema-filtered question pool ─────────────────────────────────────────────

_Q_ENTITY_DESCRIPTION = Question(
    key="entity_description",
    prompt="What kind of records does this domain clean?",
    hint="e.g. sports event tickets, customer profiles, purchase transactions",
)
_Q_GAP_TYPES = Question(
    key="gap_types",
    prompt="What data quality gaps typically require a web search to resolve?",
    hint="e.g. unknown_team, unknown_venue, event_time_mismatch",
)
_Q_TRUSTED_SOURCES = Question(
    key="trusted_sources",
    prompt="Which authoritative websites should be searched for this domain? (comma-separated)",
    hint="e.g. nhl.com, nba.com, ticketmaster.com, wikipedia.org",
)
_Q_INDUSTRY_CONTEXT = Question(
    key="industry_context",
    prompt="Any additional domain-specific context the LLM should know?",
    hint="e.g. team name abbreviations, venue aliases — leave blank to skip",
)
_Q_TEXT_FIELDS = Question(
    key="text_fields",
    prompt="Which text fields most commonly have spelling or capitalization errors?",
    hint="already detected from schema — add any context about error patterns",
)
_Q_TEAM_ALIASES = Question(
    key="team_aliases",
    prompt="What team name aliases and abbreviations are common in this domain?",
    hint="e.g. Leafs=Toronto Maple Leafs, Habs=Montreal Canadiens, Sens=Ottawa Senators",
)
_Q_POSTAL_FORMAT = Question(
    key="postal_format",
    prompt="What postal/zip code formats are used, and which countries?",
    hint="e.g. Canadian FSA (A1A 1A1), US ZIP (12345), both",
)
_Q_DATETIME_FORMAT = Question(
    key="datetime_format",
    prompt="What timezone context applies to date/time columns?",
    hint="e.g. all times in ET, mixed timezones, UTC stored locally converted",
)

_TEXT_TYPES = frozenset({"text", "character varying", "varchar", "char", "character"})
_TIMESTAMP_TYPES = frozenset({
    "timestamp", "timestamptz", "timestamp with time zone",
    "timestamp without time zone", "date",
})
_TEAM_WORDS = frozenset({"team", "player", "athlete", "club"})
_POSTAL_WORDS = frozenset({"postal", "zip", "postcode", "zipcode"})

# ── core researcher ───────────────────────────────────────────────────────────────

class DomainResearcher:
    """Builds seed content for a new domain using LLM assistance."""

    def __init__(self, domain: str):
        self.domain = domain
        self.questions: List[Question] = _QUESTIONS

    # ── prompt building ───────────────────────────────────────────────────────────

    def build_llm_prompt(self, answers: Dict[str, str]) -> str:
        answers_text = "\n".join(
            f"  {q.key}: {answers.get(q.key, '(not provided')}"
            for q in self.questions
        )

        return textwrap.dedent(f"""
            You are a data quality expert helping initialize a new data cleaning domain.

            Domain: {self.domain}

            The user answered these questions about their domain:
            {answers_text}

            Generate seed content for this domain. Respond with a single JSON object
            (no markdown, no explanation outside JSON) with exactly these keys:

            {{
              "spell_corrections": [
                {{"wrong": "misspelled", "right": "correct", "confidence": 0.95}},
                ...  // 20-30 realistic misspellings for the text fields named above
              ],
              "query_packs": [
                {{
                  "gap_type": "gap_type_key",
                  "seed_queries": [
                    "query template using {{field_name}} placeholders",
                    ...  // 2-4 templates per gap type
                  ]
                }},
                ...  // one entry per gap type named above
              ],
              "column_descriptions": [
                {{
                  "column_name": "field_name",
                  "description": "what this field contains",
                  "example_values": ["example1", "example2"],
                  "data_type": "text|date|phone|email|numeric|code"
                }},
                ...  // one entry per field named above
              ]
            }}

            Rules:
            - spell_corrections: focus on the text_fields named above
            - gap_type keys must be valid Python identifiers (lowercase, underscores)
            - {{field_name}} placeholders in seed_queries must match field names from above
            - data_type must be exactly one of: text, date, phone, email, numeric, code
            - confidence values must be 0.0-1.0
        """).strip()

    # ── response parsing ──────────────────────────────────────────────────────────

    def parse_llm_response(self, response_text: str) -> ResearchBundle:
        # Strip markdown code fences if LLM wrapped the JSON
        clean = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean.strip())

        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse LLM response as JSON: {e}") from e

        corrections = [
            SpellCorrection(
                wrong=item["wrong"],
                right=item["right"],
                source="llm_generated",
                confidence=float(item.get("confidence", 0.90)),
            )
            for item in data.get("spell_corrections", [])
        ]

        packs = [
            QueryPack(
                gap_type=item["gap_type"],
                seed_queries=item.get("seed_queries", []),
            )
            for item in data.get("query_packs", [])
        ]

        columns = [
            ColumnDescription(
                column_name=item["column_name"],
                description=item.get("description", ""),
                example_values=item.get("example_values", []),
                data_type=item.get("data_type", "text"),
            )
            for item in data.get("column_descriptions", [])
        ]

        return ResearchBundle(
            domain=self.domain,
            spell_corrections=corrections,
            query_packs=packs,
            column_descriptions=columns,
        )

    # ── LLM call ──────────────────────────────────────────────────────────────────

    def research(
        self,
        answers: Dict[str, str],
        llm_client: Any,
        model: str,
    ) -> ResearchBundle:
        prompt = self.build_llm_prompt(answers)
        response = llm_client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        _log_usage(model, getattr(response, "usage", None))
        text_block = next(
            (block for block in response.content if hasattr(block, "text")),
            None,
        )
        if text_block is None:
            raise ValueError(
                f"No text block found in LLM response. "
                f"Block types received: {[type(b).__name__ for b in response.content]}"
            )
        return self.parse_llm_response(text_block.text)

    # ── file writing ──────────────────────────────────────────────────────────────

    def write_seeds(
        self,
        bundle: ResearchBundle,
        output_dir: Path,
        dry_run: bool = False,
        force: bool = False,
    ) -> List[str]:
        """Write seed files to output_dir. Returns list of written paths."""
        if dry_run:
            return []

        written = []

        # spell_corrections.csv
        csv_path = output_dir / "spell_corrections.csv"
        if force or not csv_path.exists():
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["wrong", "right", "source", "confidence"])
                writer.writeheader()
                for sc in bundle.spell_corrections:
                    writer.writerow({
                        "wrong": sc.wrong,
                        "right": sc.right,
                        "source": sc.source,
                        "confidence": sc.confidence,
                    })
            written.append(str(csv_path))

        # query_packs.yaml
        qp_path = output_dir / "query_packs.yaml"
        if force or not qp_path.exists():
            gap_types = {
                pack.gap_type: {"seed_queries": pack.seed_queries}
                for pack in bundle.query_packs
            }
            with qp_path.open("w", encoding="utf-8") as f:
                yaml.dump(
                    {"domain": bundle.domain, "gap_types": gap_types},
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                )
            written.append(str(qp_path))

        # column_metadata.yaml
        meta_path = output_dir / "column_metadata.yaml"
        if force or not meta_path.exists():
            meta = [
                {
                    "column_name": cd.column_name,
                    "description": cd.description,
                    "example_values": cd.example_values,
                    "data_type": cd.data_type,
                }
                for cd in bundle.column_descriptions
            ]
            with meta_path.open("w", encoding="utf-8") as f:
                yaml.dump(meta, f, default_flow_style=False, allow_unicode=True)
            written.append(str(meta_path))

        return written

    # ── schema-aware methods ──────────────────────────────────────────────────────

    def get_filtered_questions(self, schema: Dict[str, List[Dict]]) -> List[Question]:
        """Return Q&A questions relevant to the columns present in schema."""
        all_col_names: set = set()
        all_col_types: set = set()
        for cols in schema.values():
            for col in cols:
                all_col_names.add(col["name"].lower())
                all_col_types.add(col["type"].lower())

        questions = [_Q_ENTITY_DESCRIPTION, _Q_GAP_TYPES, _Q_TRUSTED_SOURCES]

        if all_col_types & _TEXT_TYPES:
            questions.append(_Q_TEXT_FIELDS)

        if any(word in col for col in all_col_names for word in _TEAM_WORDS):
            questions.append(_Q_TEAM_ALIASES)

        if any(word in col for col in all_col_names for word in _POSTAL_WORDS):
            questions.append(_Q_POSTAL_FORMAT)

        if all_col_types & _TIMESTAMP_TYPES:
            questions.append(_Q_DATETIME_FORMAT)

        questions.append(_Q_INDUSTRY_CONTEXT)
        return questions

    def build_schema_prompt(
        self,
        schema: Dict[str, List[Dict]],
        annotations: Dict[str, str],
        data_samples: Dict[str, List],
        answers: Dict[str, str],
    ) -> str:
        """Build LLM prompt grounded in schema, annotations, and actual data samples."""
        schema_lines = []
        for table, cols in schema.items():
            schema_lines.append(f"\nTable: {table}")
            for col in cols:
                ann = annotations.get(f"{table}.{col['name']}", "")
                ann_note = f"  — {ann}" if ann else ""
                sample_key = f"{table}.{col['name']}"
                samples = data_samples.get(sample_key, [])
                if samples:
                    sample_note = f"  [samples: {', '.join(str(s) for s in samples[:5])}]"
                else:
                    sample_note = "  [no data yet]"
                schema_lines.append(
                    f"  {col['name']} ({col['type']}){ann_note}{sample_note}"
                )
        schema_block = "\n".join(schema_lines)

        answers_text = "\n".join(
            f"  {k}: {v}" for k, v in answers.items() if v
        )

        return textwrap.dedent(f"""
            You are a data quality expert helping initialize a new data cleaning domain.

            Domain: {self.domain}

            === ACTUAL DATABASE SCHEMA (use these exact column names) ===
            {schema_block}

            === USER CONTEXT ===
            {answers_text}

            Generate seed content for this domain. Use ONLY the column names shown above.
            Respond with a single JSON object (no markdown, no explanation outside JSON)
            with exactly these keys:

            {{
              "spell_corrections": [
                {{"wrong": "misspelled", "right": "correct", "confidence": 0.95}},
                ...  // 15-25 evidence-based corrections for text columns that have data samples above
                     // SKIP columns with [no data yet] — do not guess
              ],
              "query_packs": [
                {{
                  "gap_type": "gap_type_key",
                  "seed_queries": [
                    "query template using {{field_name}} placeholders matching schema above",
                    ...
                  ]
                }},
                ...
              ],
              "column_descriptions": [
                {{
                  "column_name": "exact_column_name_from_schema",
                  "description": "what this field contains",
                  "example_values": ["example1", "example2"],
                  "data_type": "text|date|phone|email|numeric|code"
                }},
                ...
              ]
            }}

            Rules:
            - spell_corrections: only for columns that have data samples shown above
            - column names in column_descriptions must exactly match names in the schema above
            - gap_type keys must be valid Python identifiers (lowercase, underscores)
            - {{field_name}} placeholders in seed_queries must match column names from schema
            - confidence values must be 0.0-1.0
            - data_type must be exactly one of: text, date, phone, email, numeric, code
        """).strip()

    def research_with_schema(
        self,
        answers: Dict[str, str],
        schema: Dict[str, List[Dict]],
        annotations: Dict[str, str],
        data_samples: Dict[str, List],
        llm_client: Any,
        model: str,
    ) -> ResearchBundle:
        """Research with schema context — grounded in actual columns and data samples."""
        prompt = self.build_schema_prompt(schema, annotations, data_samples, answers)
        response = llm_client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        _log_usage(model, getattr(response, "usage", None))
        text_block = next(
            (block for block in response.content if hasattr(block, "text")), None
        )
        if text_block is None:
            raise ValueError(
                f"No text block in LLM response. "
                f"Block types: {[type(b).__name__ for b in response.content]}"
            )
        return self.parse_llm_response(text_block.text)
