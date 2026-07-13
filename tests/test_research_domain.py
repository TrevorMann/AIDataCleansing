"""Tests for the domain researcher — TDD-first, LLM calls mocked."""

import csv
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from seeders.domain_researcher import (
    DomainResearcher,
    ResearchBundle,
    SpellCorrection,
    QueryPack,
    ColumnDescription,
)


# ── LLM response parsing ─────────────────────────────────────────────────────────

_VALID_LLM_RESPONSE = json.dumps({
    "spell_corrections": [
        {"wrong": "Toranto", "right": "Toronto", "confidence": 0.99},
        {"wrong": "Stadeum", "right": "Stadium", "confidence": 0.95},
    ],
    "query_packs": [
        {
            "gap_type": "venue_unresolved",
            "seed_queries": [
                "site:seatgeek.com {venue_name} {city}",
                "{venue_name} {city} arena address",
            ],
        }
    ],
    "column_descriptions": [
        {
            "column_name": "venue_name",
            "description": "Name of the event venue or arena",
            "example_values": ["Madison Square Garden", "Staples Center"],
            "data_type": "text",
        }
    ],
})


class TestParseResponse:
    def test_parses_spell_corrections(self):
        r = DomainResearcher(domain="sports_ticketing")
        bundle = r.parse_llm_response(_VALID_LLM_RESPONSE)
        assert len(bundle.spell_corrections) == 2
        assert bundle.spell_corrections[0].wrong == "Toranto"
        assert bundle.spell_corrections[0].right == "Toronto"

    def test_parses_query_packs(self):
        r = DomainResearcher(domain="sports_ticketing")
        bundle = r.parse_llm_response(_VALID_LLM_RESPONSE)
        assert len(bundle.query_packs) == 1
        assert bundle.query_packs[0].gap_type == "venue_unresolved"
        assert len(bundle.query_packs[0].seed_queries) == 2

    def test_parses_column_descriptions(self):
        r = DomainResearcher(domain="sports_ticketing")
        bundle = r.parse_llm_response(_VALID_LLM_RESPONSE)
        assert len(bundle.column_descriptions) == 1
        assert bundle.column_descriptions[0].column_name == "venue_name"
        assert bundle.column_descriptions[0].data_type == "text"

    def test_spell_correction_defaults_source_to_llm_generated(self):
        r = DomainResearcher(domain="sports_ticketing")
        bundle = r.parse_llm_response(_VALID_LLM_RESPONSE)
        assert bundle.spell_corrections[0].source == "llm_generated"

    def test_strips_markdown_code_block(self):
        r = DomainResearcher(domain="sports_ticketing")
        wrapped = f"```json\n{_VALID_LLM_RESPONSE}\n```"
        bundle = r.parse_llm_response(wrapped)
        assert len(bundle.spell_corrections) == 2

    def test_malformed_json_raises_value_error(self):
        r = DomainResearcher(domain="sports_ticketing")
        with pytest.raises(ValueError, match="parse"):
            r.parse_llm_response("not valid json {{{")

    def test_empty_sections_return_empty_lists(self):
        r = DomainResearcher(domain="sports_ticketing")
        bundle = r.parse_llm_response(json.dumps({
            "spell_corrections": [],
            "query_packs": [],
            "column_descriptions": [],
        }))
        assert bundle.spell_corrections == []
        assert bundle.query_packs == []


# ── file writing ─────────────────────────────────────────────────────────────────

def _make_bundle(domain="test_domain"):
    return ResearchBundle(
        domain=domain,
        spell_corrections=[
            SpellCorrection("Toranto", "Toronto", "llm_generated", 0.99),
            SpellCorrection("Stadeum", "Stadium", "llm_generated", 0.95),
        ],
        query_packs=[
            QueryPack("venue_unresolved", [
                "site:seatgeek.com {venue_name} {city}",
                "{venue_name} {city} arena",
            ]),
        ],
        column_descriptions=[
            ColumnDescription("venue_name", "Event venue name", ["MSG", "Staples"], "text"),
        ],
    )


class TestWriteSeeds:
    def test_writes_spell_corrections_csv(self):
        bundle = _make_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            r = DomainResearcher(domain="test_domain")
            r.write_seeds(bundle, output_dir=Path(tmp), dry_run=False)
            csv_path = Path(tmp) / "spell_corrections.csv"
            assert csv_path.exists()
            rows = list(csv.DictReader(csv_path.open()))
            assert len(rows) == 2
            assert rows[0]["wrong"] == "Toranto"
            assert rows[0]["right"] == "Toronto"

    def test_spell_corrections_csv_has_confidence_column(self):
        bundle = _make_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            r = DomainResearcher(domain="test_domain")
            r.write_seeds(bundle, output_dir=Path(tmp), dry_run=False)
            rows = list(csv.DictReader((Path(tmp) / "spell_corrections.csv").open()))
            assert "confidence" in rows[0]

    def test_writes_query_packs_yaml(self):
        bundle = _make_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            r = DomainResearcher(domain="test_domain")
            r.write_seeds(bundle, output_dir=Path(tmp), dry_run=False)
            qp = yaml.safe_load((Path(tmp) / "query_packs.yaml").open())
            assert qp["domain"] == "test_domain"
            assert "venue_unresolved" in qp["gap_types"]
            assert len(qp["gap_types"]["venue_unresolved"]["seed_queries"]) == 2

    def test_writes_column_metadata_yaml(self):
        bundle = _make_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            r = DomainResearcher(domain="test_domain")
            r.write_seeds(bundle, output_dir=Path(tmp), dry_run=False)
            meta = yaml.safe_load((Path(tmp) / "column_metadata.yaml").open())
            assert isinstance(meta, list)
            assert meta[0]["column_name"] == "venue_name"

    def test_dry_run_writes_no_files(self):
        bundle = _make_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            r = DomainResearcher(domain="test_domain")
            r.write_seeds(bundle, output_dir=Path(tmp), dry_run=True)
            assert not any(Path(tmp).iterdir())

    def test_does_not_overwrite_existing_file_without_force(self):
        bundle = _make_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "spell_corrections.csv"
            existing.write_text("original content")
            r = DomainResearcher(domain="test_domain")
            r.write_seeds(bundle, output_dir=Path(tmp), dry_run=False, force=False)
            assert existing.read_text() == "original content"

    def test_overwrites_existing_file_with_force(self):
        bundle = _make_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "spell_corrections.csv"
            existing.write_text("original content")
            r = DomainResearcher(domain="test_domain")
            r.write_seeds(bundle, output_dir=Path(tmp), dry_run=False, force=True)
            assert existing.read_text() != "original content"


import json as _json

_SCHEMA = {
    "events": [
        {"name": "event_id",       "type": "uuid",         "notnull": True,  "pk": True},
        {"name": "event_name",     "type": "text",         "notnull": False, "pk": False},
        {"name": "home_team",      "type": "text",         "notnull": False, "pk": False},
        {"name": "start_datetime", "type": "timestamptz",  "notnull": False, "pk": False},
    ],
    "customers": [
        {"name": "customer_id",    "type": "uuid",         "notnull": True,  "pk": True},
        {"name": "postal_code",    "type": "text",         "notnull": False, "pk": False},
        {"name": "city",           "type": "text",         "notnull": False, "pk": False},
    ],
}
_ANNOTATIONS = {
    "events.event_name":     "Name of the sports event",
    "events.home_team":      "Home team name",
    "customers.postal_code": "Customer postal/zip code",
}
_SAMPLES = {
    "events.home_team":      ["Leafs", "Raptors", "Blue Jays"],
    "events.event_name":     ["Leafs vs Sens", "Raptors vs Celtics"],
    "customers.postal_code": [],
}


class TestGetFilteredQuestions:
    def test_always_includes_gap_types_question(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "gap_types" in keys

    def test_always_includes_trusted_sources_question(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "trusted_sources" in keys

    def test_includes_team_aliases_when_team_column_exists(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "team_aliases" in keys

    def test_includes_postal_format_when_postal_column_exists(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "postal_format" in keys

    def test_includes_datetime_format_when_timestamp_column_exists(self):
        r = DomainResearcher(domain="sports_ticketing")
        questions = r.get_filtered_questions(_SCHEMA)
        keys = {q.key for q in questions}
        assert "datetime_format" in keys

    def test_skips_postal_when_no_postal_column(self):
        r = DomainResearcher(domain="sports_ticketing")
        schema_no_postal = {
            "events": [{"name": "event_name", "type": "text", "notnull": False, "pk": False}]
        }
        questions = r.get_filtered_questions(schema_no_postal)
        keys = {q.key for q in questions}
        assert "postal_format" not in keys

    def test_skips_team_aliases_when_no_team_column(self):
        r = DomainResearcher(domain="sports_ticketing")
        schema_no_team = {
            "customers": [{"name": "email", "type": "text", "notnull": False, "pk": False}]
        }
        questions = r.get_filtered_questions(schema_no_team)
        keys = {q.key for q in questions}
        assert "team_aliases" not in keys


class TestResearchWithSchema:
    def test_returns_research_bundle(self):
        r = DomainResearcher(domain="sports_ticketing")
        answers = {
            "gap_types": "unknown_team, unknown_venue",
            "trusted_sources": "nhl.com, ticketmaster.com",
            "industry_context": "",
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        bundle = r.research_with_schema(
            answers=answers,
            schema=_SCHEMA,
            annotations=_ANNOTATIONS,
            data_samples=_SAMPLES,
            llm_client=mock_client,
            model="claude-test",
        )
        assert isinstance(bundle, ResearchBundle)
        # one call per artifact: spell_corrections, query_packs, column_descriptions
        assert mock_client.messages.create.call_count == 3

    def test_schema_context_appears_in_llm_prompt(self):
        r = DomainResearcher(domain="sports_ticketing")
        answers = {"gap_types": "x", "trusted_sources": "y", "industry_context": ""}
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        r.research_with_schema(
            answers=answers, schema=_SCHEMA, annotations=_ANNOTATIONS,
            data_samples=_SAMPLES, llm_client=mock_client, model="test",
        )
        call_args = mock_client.messages.create.call_args
        messages = call_args[1].get("messages") or call_args[0][1]
        content = str(messages)
        assert "home_team" in content or "events" in content

    def test_skips_spell_correction_llm_call_when_no_samples(self):
        """No column anywhere has data — don't even ask the LLM for corrections,
        so a prompt-instruction-ignoring model can't hallucinate any."""
        r = DomainResearcher(domain="sports_ticketing")
        empty_samples = {k: [] for k in _SAMPLES}
        # A response with no "spell_corrections" key — simulates the LLM only ever
        # being asked for query_packs/column_descriptions, never spell_corrections.
        no_corrections_response = json.dumps({
            k: v for k, v in json.loads(_VALID_LLM_RESPONSE).items()
            if k != "spell_corrections"
        })
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=no_corrections_response)]
        mock_client.messages.create.return_value = mock_response

        bundle = r.research_with_schema(
            answers={"gap_types": "x", "trusted_sources": "y", "industry_context": ""},
            schema=_SCHEMA,
            annotations=_ANNOTATIONS,
            data_samples=empty_samples,
            llm_client=mock_client,
            model="test",
        )
        # only query_packs + column_descriptions calls, spell_corrections skipped
        assert mock_client.messages.create.call_count == 2
        assert bundle.spell_corrections == []

    def test_still_generates_spell_corrections_when_any_column_has_samples(self):
        r = DomainResearcher(domain="sports_ticketing")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        r.research_with_schema(
            answers={"gap_types": "x", "trusted_sources": "y", "industry_context": ""},
            schema=_SCHEMA,
            annotations=_ANNOTATIONS,
            data_samples=_SAMPLES,  # home_team/event_name have samples, postal_code doesn't
            llm_client=mock_client,
            model="test",
        )
        assert mock_client.messages.create.call_count == 3


# ── web grounding + tiered client (audit findings 1.1/1.2/1.4) ────────────────────

from seeders.domain_researcher import gather_web_context


class TestWebGroundedResearch:
    def test_gather_web_context_builds_snippet_block(self):
        cache = MagicMock()
        cache.get_or_search.return_value = {
            "results": [{"content": "Scotiabank Arena is home of the Maple Leafs"}]
        }
        ctx = gather_web_context(
            "sports_ticketing",
            {"entity_description": "hockey tickets", "gap_types": "unknown_venue"},
            cache,
        )
        assert "Scotiabank Arena" in ctx
        assert cache.get_or_search.call_count == 2  # misspellings + 1 gap query

    def test_gather_web_context_survives_search_failure(self):
        cache = MagicMock()
        cache.get_or_search.side_effect = RuntimeError("tavily down")
        ctx = gather_web_context("d", {"entity_description": "x"}, cache)
        assert ctx == ""

    def test_web_context_appears_in_research_prompt(self):
        r = DomainResearcher(domain="sports_ticketing")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        r.research_with_schema(
            answers={}, schema=_SCHEMA, annotations={}, data_samples={},
            llm_client=mock_client, model="test",
            web_context="[search: venues]\nScotiabank Arena facts",
        )
        content = str(mock_client.messages.create.call_args_list[0])
        assert "Scotiabank Arena" in content

    def test_tiered_llm_client_path(self):
        """Passing a tiered LLMClient uses messages_create (retry+usage built in)."""
        r = DomainResearcher(domain="sports_ticketing")
        llm = MagicMock()
        resp = MagicMock()
        resp.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        llm.messages_create.return_value = resp

        bundle = r.research_with_schema(
            answers={}, schema=_SCHEMA, annotations={}, data_samples=_SAMPLES, llm=llm,
        )
        assert isinstance(bundle, ResearchBundle)
        assert llm.messages_create.call_count == 3
