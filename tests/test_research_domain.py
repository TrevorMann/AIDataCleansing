"""Tests for the domain researcher — TDD-first, LLM calls mocked."""

import csv
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from seeders.domain_researcher import (
    DomainResearcher,
    Question,
    ResearchBundle,
    SpellCorrection,
    QueryPack,
    ColumnDescription,
)


# ── questionnaire structure ──────────────────────────────────────────────────────

class TestQuestionnaire:
    def test_researcher_has_questions(self):
        r = DomainResearcher(domain="test_domain")
        assert len(r.questions) >= 5

    def test_each_question_has_key_and_prompt(self):
        r = DomainResearcher(domain="test_domain")
        for q in r.questions:
            assert isinstance(q, Question)
            assert q.key, "Question must have a non-empty key"
            assert q.prompt, "Question must have a non-empty prompt"
            assert "?" in q.prompt, "Prompt should be a question"

    def test_questions_cover_entity_type(self):
        r = DomainResearcher(domain="test_domain")
        keys = {q.key for q in r.questions}
        assert "entity_description" in keys

    def test_questions_cover_fields(self):
        r = DomainResearcher(domain="test_domain")
        keys = {q.key for q in r.questions}
        assert "fields" in keys

    def test_questions_cover_text_fields(self):
        r = DomainResearcher(domain="test_domain")
        keys = {q.key for q in r.questions}
        assert "text_fields" in keys

    def test_questions_cover_linking_fields(self):
        r = DomainResearcher(domain="test_domain")
        keys = {q.key for q in r.questions}
        assert "linking_fields" in keys

    def test_questions_cover_gap_types(self):
        r = DomainResearcher(domain="test_domain")
        keys = {q.key for q in r.questions}
        assert "gap_types" in keys

    def test_questions_cover_trusted_sources(self):
        r = DomainResearcher(domain="test_domain")
        keys = {q.key for q in r.questions}
        assert "trusted_sources" in keys


# ── LLM prompt construction ──────────────────────────────────────────────────────

class TestBuildPrompt:
    def _answers(self):
        return {
            "entity_description": "sports event tickets",
            "fields": "event_id, team_name, venue_name, event_date, ticket_type",
            "text_fields": "venue_name, team_name",
            "linking_fields": "event_id, venue_name + team_name + event_date",
            "gap_types": "missing venue address, unknown team name",
            "trusted_sources": "espn.com, ticketmaster.com, seatgeek.com",
        }

    def test_prompt_includes_domain_name(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_llm_prompt(self._answers())
        assert "sports_ticketing" in prompt

    def test_prompt_includes_entity_description(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_llm_prompt(self._answers())
        assert "sports event tickets" in prompt

    def test_prompt_includes_field_names(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_llm_prompt(self._answers())
        assert "venue_name" in prompt

    def test_prompt_requests_json_output(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_llm_prompt(self._answers())
        assert "JSON" in prompt or "json" in prompt

    def test_prompt_requests_spell_corrections(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_llm_prompt(self._answers())
        assert "spell_corrections" in prompt

    def test_prompt_requests_query_packs(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_llm_prompt(self._answers())
        assert "query_packs" in prompt

    def test_prompt_requests_column_descriptions(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_llm_prompt(self._answers())
        assert "column_descriptions" in prompt


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


# ── LLM integration (mocked) ─────────────────────────────────────────────────────

class TestResearchWithMockedLLM:
    def test_research_calls_llm_and_returns_bundle(self):
        r = DomainResearcher(domain="sports_ticketing")
        answers = {
            "entity_description": "event tickets",
            "fields": "event_id, venue_name",
            "text_fields": "venue_name",
            "linking_fields": "event_id",
            "gap_types": "missing venue",
            "trusted_sources": "ticketmaster.com",
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        bundle = r.research(answers, llm_client=mock_client, model="claude-test")
        assert isinstance(bundle, ResearchBundle)
        assert len(bundle.spell_corrections) == 2
        mock_client.messages.create.assert_called_once()

    def test_research_prompt_passed_to_llm(self):
        r = DomainResearcher(domain="sports_ticketing")
        answers = {
            "entity_description": "event tickets",
            "fields": "venue_name",
            "text_fields": "venue_name",
            "linking_fields": "event_id",
            "gap_types": "missing venue",
            "trusted_sources": "ticketmaster.com",
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_VALID_LLM_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        r.research(answers, llm_client=mock_client, model="claude-test")
        call_kwargs = mock_client.messages.create.call_args
        messages = call_kwargs[1]["messages"] if call_kwargs[1] else call_kwargs[0][1]
        assert any("sports_ticketing" in str(m) for m in messages)


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


class TestBuildSchemaPrompt:
    def test_prompt_includes_schema_summary(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        assert "events" in prompt
        assert "home_team" in prompt

    def test_prompt_includes_annotation_descriptions(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        assert "Name of the sports event" in prompt

    def test_prompt_includes_data_samples(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        assert "Leafs" in prompt or "Raptors" in prompt

    def test_prompt_notes_empty_sample_columns(self):
        r = DomainResearcher(domain="sports_ticketing")
        prompt = r.build_schema_prompt(_SCHEMA, _ANNOTATIONS, _SAMPLES, {})
        # postal_code has 0 samples — should be noted
        assert "postal_code" in prompt


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
        mock_client.messages.create.assert_called_once()

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
