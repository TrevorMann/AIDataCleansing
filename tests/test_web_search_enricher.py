"""Tests for C2: WebSearchEnricher skill."""

from unittest.mock import MagicMock, patch
import pytest

from skills._common.web_search_enricher.web_search_enricher import WebSearchEnricher
from skills._common.web_search_enricher.parsers.real_estate import postal_unresolved, municipality_ambiguous, unknown_country
from skills._common.web_search_enricher.parsers._common import unknown_phone_format, unknown_email_domain


# --- Parser unit tests ---

class TestPostalUnresolvedParser:
    def test_finds_known_municipality(self):
        result = postal_unresolved.parse("M9L is a postal code in North York, Toronto area", {})
        assert result is not None
        assert result["fields"]["municipality"] == "North York"
        assert result["confidence"] == 0.75

    def test_returns_none_when_no_match(self):
        assert postal_unresolved.parse("Nothing useful here", {}) is None

    def test_returns_none_on_empty(self):
        assert postal_unresolved.parse("", {}) is None

    def test_captures_url(self):
        result = postal_unresolved.parse(
            "M9L is in Scarborough. URL: https://canadapost.ca/postal/M9L", {}
        )
        assert result is not None
        assert result["source_url"] is not None


class TestMunicipalityAmbiguousParser:
    def test_finds_municipality_phrase(self):
        result = municipality_ambiguous.parse(
            "The municipality of Markham is located in York Region.", {}
        )
        assert result is not None
        assert "Markham" in result["fields"]["municipality"]

    def test_returns_none_on_empty(self):
        assert municipality_ambiguous.parse("", {}) is None


class TestUnknownCountryParser:
    def test_detects_canada(self):
        result = unknown_country.parse("This address is in Canada", {})
        assert result is not None
        assert result["fields"]["country"] == "CA"

    def test_detects_usa(self):
        result = unknown_country.parse("Located in the United States", {})
        assert result is not None
        assert result["fields"]["country"] == "US"

    def test_returns_none_on_no_match(self):
        assert unknown_country.parse("Unknown location", {}) is None


# --- WebSearchEnricher skill ---

def _make_enricher(queries=None, cache_result=None, conn=None):
    enricher = WebSearchEnricher({"max_queries": 3, "trigger_below": 0.70})
    enricher.domain = "real_estate"
    if conn:
        enricher.conn = conn
    if cache_result is not None:
        mock_cache = MagicMock()
        mock_cache.get_or_search.return_value = cache_result
        enricher.cache = mock_cache
    if queries is not None:
        enricher._get_queries = lambda gap: queries
    return enricher


def test_enricher_skips_when_triage_done():
    enricher = _make_enricher()
    record = {"_triage_route": "done", "_triage_data_confidence": 0.30}
    result = enricher.run(record)
    assert "_web_search_evidence" not in result


def test_enricher_skips_when_triage_unsalvageable():
    enricher = _make_enricher()
    record = {"_triage_route": "unsalvageable", "_triage_data_confidence": 0.10}
    result = enricher.run(record)
    assert "_web_search_evidence" not in result


def test_enricher_skips_when_confidence_high():
    enricher = _make_enricher()
    record = {"_triage_route": "needs_review", "_triage_data_confidence": 0.90}
    result = enricher.run(record)
    assert "_web_search_evidence" not in result


def test_enricher_skips_when_no_gaps():
    enricher = _make_enricher()
    record = {
        "_triage_route": "needs_review",
        "_triage_data_confidence": 0.50,
        "country": "CA",
        "_municipality_confidence": 0.85,
    }
    result = enricher.run(record)
    assert "_web_search_evidence" not in result


def test_enricher_resolves_postal_via_search():
    enricher = _make_enricher(
        queries=["site:canadapost.ca {postal_code}"],
        cache_result="M9L is in North York area. https://canadapost.ca/lookup",
    )
    record = {
        "_triage_route": "needs_review",
        "_triage_data_confidence": 0.50,
        "_unknown_fsa": "M9L",
        "postal_code": "M9L 1H7",
    }
    result = enricher.run(record)
    assert result["municipality"] == "North York"
    assert len(result["_web_search_evidence"]) == 1
    assert "_decisions" in result


def test_enricher_no_parsable_result_logs_decision():
    enricher = _make_enricher(
        queries=["query {postal_code}"],
        cache_result="Totally unhelpful content with no city names",
    )
    record = {
        "_triage_route": "needs_review",
        "_triage_data_confidence": 0.50,
        "_unknown_fsa": "X9X",
        "postal_code": "X9X 0X0",
    }
    result = enricher.run(record)
    assert result.get("_web_search_evidence") == []
    assert any("no parsable" in d.get("decision", "").lower() for d in result.get("_decisions", []))


def test_enricher_budget_exhaustion_skips():
    from cleaning.orchestrator_v2 import BatchBudget
    budget = BatchBudget(0)  # empty budget
    enricher = _make_enricher(
        queries=["query {postal_code}"],
        cache_result="North York content here",
    )
    record = {
        "_triage_route": "needs_review",
        "_triage_data_confidence": 0.50,
        "_unknown_fsa": "M9L",
        "postal_code": "M9L 1H7",
    }
    result = enricher.run(record, tools={"batch_budget": budget})
    assert "municipality" not in result or result.get("municipality") is None
    decisions = result.get("_decisions", [])
    assert any("budget" in d.get("decision", "").lower() for d in decisions)


def test_enricher_no_cache_returns_no_evidence():
    enricher = WebSearchEnricher({"max_queries": 3, "trigger_below": 0.70})
    enricher.domain = "real_estate"
    enricher._get_queries = lambda gap: ["{postal_code} municipality"]
    record = {
        "_triage_route": "needs_review",
        "_triage_data_confidence": 0.50,
        "_unknown_fsa": "M9L",
        "postal_code": "M9L 1H7",
    }
    result = enricher.run(record)
    assert result.get("_web_search_evidence") == []


def test_skill_registry_loads_web_search_enricher():
    from skills.registry import SkillRegistry
    registry = SkillRegistry.load("real_estate")
    skill = registry.get("web_search_enricher")
    assert skill is not None
    assert skill.name == "WebSearchEnricher"
