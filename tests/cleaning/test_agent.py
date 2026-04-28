"""Tests for cleaning.agent.

Includes needs_escalation predicate (this task) and CleaningAgent (Task 9).
"""
from cleaning.flags import FlagType
from cleaning.types import CleaningOutput


def _output_with(record: dict) -> CleaningOutput:
    return CleaningOutput(cleaned_record=record)


def test_needs_escalation_unknown_country():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "", "postal_code": "M5V 1A1", "municipality": "Toronto"})
    assert FlagType.UNKNOWN_COUNTRY in needs_escalation(out)


def test_needs_escalation_postal_unresolved():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "N/A", "municipality": "Toronto"})
    assert FlagType.POSTAL_UNRESOLVED in needs_escalation(out)


def test_needs_escalation_postal_ambiguous():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "M6H ?", "municipality": "Toronto"})
    assert FlagType.POSTAL_AMBIGUOUS in needs_escalation(out)


def test_needs_escalation_municipality_unresolved():
    from cleaning.agent import needs_escalation
    out = _output_with({"country": "Canada", "postal_code": "M6H 1E7", "municipality": "N/A"})
    assert FlagType.MUNICIPALITY_UNRESOLVED in needs_escalation(out)


def test_needs_escalation_low_confidence_in_notes():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "M6H 1E7", "municipality": "Toronto",
        "validation_notes": "Confidence: LOW; could not verify",
    })
    assert FlagType.LOW_CONFIDENCE_RESEARCH in needs_escalation(out)


def test_needs_escalation_clean_record_returns_empty():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "M6H 1E7", "municipality": "Toronto",
        "validation_notes": "Confidence: HIGH",
    })
    assert needs_escalation(out) == []


def test_needs_escalation_returns_multiple_when_applicable():
    from cleaning.agent import needs_escalation
    out = _output_with({
        "country": "Canada", "postal_code": "N/A", "municipality": "N/A",
    })
    flags = needs_escalation(out)
    assert FlagType.POSTAL_UNRESOLVED in flags
    assert FlagType.MUNICIPALITY_UNRESOLVED in flags


def test_needs_escalation_country_case_insensitive():
    from cleaning.agent import needs_escalation
    # Lowercase country should NOT trigger UNKNOWN_COUNTRY
    out = _output_with({"country": "canada", "postal_code": "M6H 1E7", "municipality": "Toronto"})
    assert FlagType.UNKNOWN_COUNTRY not in needs_escalation(out)


# ---- CleaningAgent tests ----

import pytest
from unittest.mock import MagicMock


def _mock_response(text=None, tool_calls=None):
    """Build a fake Anthropic-style response."""
    resp = MagicMock()
    resp.content = []
    if text is not None:
        block = MagicMock()
        block.type = "text"
        block.text = text
        del block.name  # so "hasattr(b, 'name')" tests don't pass for text blocks
        resp.content.append(block)
    if tool_calls:
        for name, inp, tid in tool_calls:
            block = MagicMock()
            block.type = "tool_use"
            block.name = name
            block.input = inp
            block.id = tid
            resp.content.append(block)
    resp.stop_reason = "tool_use" if tool_calls else "end_turn"
    return resp


def test_cleaning_agent_no_research_needed_returns_pre_cleaned(mock_llm):
    """If a record arrives with all fields resolved, agent returns it untouched."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache
    escalator = MagicMock()
    escalator.investigate.return_value = None  # not called

    agent = CleaningAgent(
        country_code="CA",
        system_prompt="sys",
        research_prompt_builder=lambda c, t: "research please",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm,
        web_cache=WebSearchCache(),
        escalator=escalator,
    )
    mock_llm.messages_create.return_value = _mock_response(
        text="| ID | Postal Code | Municipality | Validation Notes |\n"
             "| 1 | M6H 1E7 | The Annex | Confidence: HIGH |"
    )
    record = {"id": 1, "country": "Canada", "postal_code": "M6H 1E7", "municipality": "The Annex"}
    outputs = agent.process([record])
    assert len(outputs) == 1
    assert outputs[0].cleaned_record["id"] == 1
    escalator.investigate.assert_not_called()


def test_cleaning_agent_calls_web_search_via_cache(mock_llm, mock_tavily):
    """Tool-use loop dispatches web_search through the cache."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache
    cache = WebSearchCache()
    escalator = MagicMock()
    mock_tavily.return_value = "search result"

    mock_llm.messages_create.side_effect = [
        _mock_response(tool_calls=[("web_search", {"query": "M6H neighbourhood"}, "t1")]),
        _mock_response(text="| ID | Postal Code | Municipality | Validation Notes |\n"
                            "| 7 | M6H 1E7 | The Annex | HIGH |"),
    ]
    agent = CleaningAgent(
        country_code="CA", system_prompt="sys",
        research_prompt_builder=lambda c, t: "research",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm, web_cache=cache, escalator=escalator,
    )
    record = {"id": 7, "country": "Canada", "postal_code": "M6H", "municipality": "N/A",
              "address": "25 Muir Ave", "city": "Toronto", "state_province": "Ontario"}
    outputs = agent.process([record])
    assert len(outputs) == 1
    mock_tavily.assert_called_once()
    assert cache.stats()["misses"] == 1


def test_cleaning_agent_escalates_unresolved_record(mock_llm):
    """A record that comes back with N/A municipality triggers escalator.investigate."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache
    from cleaning.types import CleaningOutput
    from cleaning.flags import Flag, FlagType, FlagSeverity

    escalator = MagicMock()
    escalator.investigate.return_value = CleaningOutput(
        cleaned_record={"id": 9, "country": "Canada", "postal_code": "M6H 1E7",
                        "municipality": "The Annex", "validation_notes": "resolved"},
        flags=[Flag(FlagType.RESOLVED_AFTER_ESCALATION, FlagSeverity.INFO,
                    "agent escalated and resolved", "escalator")],
    )
    mock_llm.messages_create.return_value = _mock_response(
        text="| ID | Postal Code | Municipality | Validation Notes |\n"
             "| 9 | N/A | N/A | LOW could not resolve |"
    )
    agent = CleaningAgent(
        country_code="CA", system_prompt="sys",
        research_prompt_builder=lambda c, t: "research",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm, web_cache=WebSearchCache(), escalator=escalator,
    )
    record = {"id": 9, "country": "Canada", "postal_code": "M6H", "municipality": "N/A",
              "address": "25 Muir Ave", "city": "Toronto", "state_province": "Ontario"}
    outputs = agent.process([record])
    escalator.investigate.assert_called_once()
    assert outputs[0].cleaned_record["municipality"] == "The Annex"
    assert any(f.flag_type == FlagType.RESOLVED_AFTER_ESCALATION for f in outputs[0].flags)


def test_cleaning_agent_max_rounds_rescue(mock_llm):
    """If model loops past max_rounds, agent falls back to force-final-output."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache

    mock_llm.messages_create.side_effect = (
        [_mock_response(tool_calls=[("web_search", {"query": "x"}, f"t{i}")])
         for i in range(5)]
        + [_mock_response(text="| ID | Postal Code | Municipality | Validation Notes |\n"
                               "| 1 | M6H 1E7 | The Annex | LOW |")]
    )
    agent = CleaningAgent(
        country_code="CA", system_prompt="sys",
        research_prompt_builder=lambda c, t: "research",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm, web_cache=WebSearchCache(),
        escalator=MagicMock(), max_rounds=3,
    )
    record = {"id": 1, "country": "Canada", "postal_code": "M6H", "municipality": "N/A"}
    outputs = agent.process([record])
    # 3 rounds + 1 force-final = 4 calls total
    assert mock_llm.messages_create.call_count == 4
    assert outputs  # got an output despite the rescue


def test_cleaning_agent_search_log_resets_between_process_calls(mock_llm, mock_tavily):
    """Search log must not bleed between consecutive process() calls."""
    from cleaning.agent import CleaningAgent
    from cleaning.cache import WebSearchCache
    mock_tavily.side_effect = lambda q, max_results=5: f"r:{q}"

    mock_llm.messages_create.side_effect = [
        # batch 1: one tool call + finish
        _mock_response(tool_calls=[("web_search", {"query": "q1"}, "t1")]),
        _mock_response(text="| ID | Postal Code | Municipality | Validation Notes |\n| 1 | M6H 1E7 | Toronto | HIGH |"),
        # batch 2: finish immediately (no tool call)
        _mock_response(text="| ID | Postal Code | Municipality | Validation Notes |\n| 2 | V6B 2W9 | Vancouver | HIGH |"),
    ]
    agent = CleaningAgent(
        country_code="CA", system_prompt="sys",
        research_prompt_builder=lambda c, t: "research",
        tools=[{"name": "web_search"}],
        llm_client=mock_llm, web_cache=WebSearchCache(),
        escalator=MagicMock(),
    )
    rec1 = {"id": 1, "country": "Canada", "postal_code": "M6H 1E7", "municipality": "Toronto"}
    rec2 = {"id": 2, "country": "Canada", "postal_code": "V6B 2W9", "municipality": "Vancouver"}

    outputs1 = agent.process([rec1])
    outputs2 = agent.process([rec2])

    # batch 2's search log must be empty (no tool calls in batch 2)
    assert outputs2[0].search_log == []
    # batch 1's output must still have the search
    assert len(outputs1[0].search_log) == 1
