"""Tests for the generalized DataQualityTriageAgent in skills/_common/."""

import pytest


# ── import the _common version (not yet written — tests will fail until it exists) ──

from skills._common.data_quality_triage.data_quality_triage import DataQualityTriageAgent


# ── completeness: config-driven required_fields ─────────────────────────────────

class TestCompletenessWithConfigFields:
    def test_no_required_fields_means_always_complete(self):
        """With no required_fields config, completeness should be 1.0."""
        agent = DataQualityTriageAgent(config={})
        assert agent._evaluate_completeness({}) == 1.0

    def test_all_required_fields_present(self):
        agent = DataQualityTriageAgent(config={"required_fields": ["name", "city"]})
        assert agent._evaluate_completeness({"name": "Alice", "city": "Toronto"}) == 1.0

    def test_half_required_fields_present(self):
        agent = DataQualityTriageAgent(config={"required_fields": ["name", "city"]})
        assert agent._evaluate_completeness({"name": "Alice"}) == pytest.approx(0.5)

    def test_no_required_fields_present(self):
        agent = DataQualityTriageAgent(config={"required_fields": ["name", "city"]})
        assert agent._evaluate_completeness({}) == 0.0

    def test_empty_string_field_counts_as_missing(self):
        agent = DataQualityTriageAgent(config={"required_fields": ["name"]})
        assert agent._evaluate_completeness({"name": ""}) == 0.0

    def test_real_estate_required_fields_via_config(self):
        """Backward compat: passing real-estate fields through config works."""
        fields = ["address", "city", "postal_code", "municipality", "country"]
        agent = DataQualityTriageAgent(config={"required_fields": fields})
        complete_record = {f: "value" for f in fields}
        assert agent._evaluate_completeness(complete_record) == 1.0


# ── confidence: config-driven signal keys ───────────────────────────────────────

class TestConfidenceWithConfigSignals:
    def test_no_signals_returns_baseline(self):
        """With no matching signal keys in record, returns baseline 0.9."""
        agent = DataQualityTriageAgent(config={})
        assert agent._evaluate_confidence({}) == pytest.approx(0.9)

    def test_reads_custom_confidence_signal_key(self):
        agent = DataQualityTriageAgent(
            config={"confidence_signal_keys": ["_venue_confidence"]}
        )
        score = agent._evaluate_confidence({"_venue_confidence": 0.6})
        assert score == pytest.approx(0.6)  # min(0.6, 0.9) = 0.6

    def test_reads_multiple_signal_keys(self):
        agent = DataQualityTriageAgent(
            config={"confidence_signal_keys": ["_venue_confidence", "_team_confidence"]}
        )
        score = agent._evaluate_confidence({
            "_venue_confidence": 0.8,
            "_team_confidence": 0.7,
        })
        assert score == pytest.approx(0.7)  # min(0.8, 0.7, 0.9)

    def test_ignores_missing_signal_keys(self):
        """Signal keys not present in the record are simply skipped."""
        agent = DataQualityTriageAgent(
            config={"confidence_signal_keys": ["_venue_confidence", "_absent_key"]}
        )
        score = agent._evaluate_confidence({"_venue_confidence": 0.75})
        assert score == pytest.approx(0.75)

    def test_boolean_validated_signal_uses_fixed_score(self):
        """Boolean _*_validated flags contribute 0.85 when True."""
        agent = DataQualityTriageAgent(
            config={"validated_signal_keys": ["_event_validated"]}
        )
        score = agent._evaluate_confidence({"_event_validated": True})
        assert score == pytest.approx(0.85)

    def test_boolean_validated_signal_ignored_when_false(self):
        agent = DataQualityTriageAgent(
            config={"validated_signal_keys": ["_event_validated"]}
        )
        score = agent._evaluate_confidence({"_event_validated": False})
        assert score == pytest.approx(0.9)  # only baseline


# ── routing decisions ────────────────────────────────────────────────────────────

class TestRoutingDecisions:
    def _make_agent(self):
        return DataQualityTriageAgent(config={
            "required_fields": ["name", "city"],
            "min_confidence_auto_complete": 0.85,
            "min_confidence_agent_review": 0.60,
        })

    def test_routes_done_when_high_confidence_and_complete(self):
        agent = self._make_agent()
        result = agent.run({"name": "Alice", "city": "Toronto"})
        assert result["_triage_route"] == "done"

    def test_routes_unsalvageable_when_completeness_below_threshold(self):
        agent = self._make_agent()
        result = agent.run({"name": "Alice"})  # city missing → completeness 0.5 < 0.7
        assert result["_triage_route"] == "unsalvageable"

    def test_routes_needs_review_when_medium_confidence(self):
        agent = DataQualityTriageAgent(config={
            "required_fields": ["name", "city"],
            "confidence_signal_keys": ["_score"],
        })
        result = agent.run({"name": "Alice", "city": "Toronto", "_score": 0.70})
        assert result["_triage_route"] == "needs_review"

    def test_routes_unsalvageable_when_low_confidence(self):
        agent = DataQualityTriageAgent(config={
            "required_fields": ["name"],
            "confidence_signal_keys": ["_score"],
        })
        result = agent.run({"name": "Alice", "_score": 0.40})
        assert result["_triage_route"] == "unsalvageable"

    def test_result_contains_triage_metadata(self):
        agent = self._make_agent()
        result = agent.run({"name": "Alice", "city": "Toronto"})
        assert "_triage_confidence" in result
        assert "_triage_completeness" in result
        assert "_triage_data_confidence" in result


# ── backward compat: real-estate signals still work via config ───────────────────

class TestRealEstateCompatViaConfig:
    """Real-estate config passed through skills.yaml should produce same behavior as old class."""

    def _make_re_agent(self):
        return DataQualityTriageAgent(config={
            "required_fields": ["address", "city", "postal_code", "municipality", "country"],
            "confidence_signal_keys": ["_municipality_confidence"],
            "validated_signal_keys": ["_geographic_validated"],
            "min_confidence_auto_complete": 0.85,
            "min_confidence_agent_review": 0.60,
        })

    def test_uses_min_confidence_across_signals(self):
        agent = self._make_re_agent()
        record = {
            "address": "123 Main St", "city": "Toronto", "postal_code": "M5V",
            "municipality": "City of Toronto", "country": "CA",
            "_municipality_confidence": 0.5,
            "_geographic_validated": True,
        }
        result = agent.run(record)
        # min(0.5, 0.85, 0.9) = 0.5 — below review threshold → unsalvageable
        assert result["_triage_route"] == "unsalvageable"
        assert result["_triage_data_confidence"] == pytest.approx(0.5)

    def test_done_when_all_signals_high(self):
        agent = self._make_re_agent()
        record = {
            "address": "123 Main St", "city": "Toronto", "postal_code": "M5V",
            "municipality": "City of Toronto", "country": "CA",
            "_municipality_confidence": 0.95,
            "_geographic_validated": True,
        }
        result = agent.run(record)
        assert result["_triage_route"] == "done"
