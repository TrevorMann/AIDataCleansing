"""Tests for cleaning.escalation.EscalationAgent."""
from unittest.mock import MagicMock


def _mock_response(text=None, tool_calls=None):
    resp = MagicMock()
    resp.content = []
    if text is not None:
        block = MagicMock()
        block.type = "text"
        block.text = text
        del block.name
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


def test_escalation_unknown_country_resolves(mock_llm):
    """Escalator receives a record with no country, resolves it via web search."""
    from cleaning.escalation import EscalationAgent
    from cleaning.cache import WebSearchCache
    from cleaning.flags import FlagType
    mock_llm.messages_create.return_value = _mock_response(
        text='{"country": "Canada", "postal_code": "M6H 1E7", '
             '"municipality": "The Annex", "validation_notes": "resolved by escalation"}'
    )
    esc = EscalationAgent(llm_client=mock_llm, web_cache=WebSearchCache(),
                          tools=[{"name": "web_search"}])
    record = {"id": 5, "country": "", "postal_code": "M6H 1E7",
              "municipality": "The Annex", "address": "25 Muir Ave", "city": "Toronto"}
    out = esc.investigate(
        record=record, country_code=None,
        flag_hints=[FlagType.UNKNOWN_COUNTRY], prior_search_log=[],
    )
    assert out.cleaned_record["country"] == "Canada"
    # Successful resolution adds RESOLVED_AFTER_ESCALATION
    assert any(f.flag_type == FlagType.RESOLVED_AFTER_ESCALATION for f in out.flags)


def test_escalation_failure_persists_flags(mock_llm):
    """If escalation cannot resolve, the original hint flags are preserved."""
    from cleaning.escalation import EscalationAgent
    from cleaning.cache import WebSearchCache
    from cleaning.flags import FlagType
    mock_llm.messages_create.return_value = _mock_response(
        text='{"country": "Canada", "postal_code": "N/A", "municipality": "N/A", '
             '"validation_notes": "could not resolve"}'
    )
    esc = EscalationAgent(llm_client=mock_llm, web_cache=WebSearchCache(),
                          tools=[{"name": "web_search"}])
    record = {"id": 5, "country": "Canada", "postal_code": "N/A",
              "municipality": "N/A", "address": "x", "city": "y"}
    out = esc.investigate(
        record=record, country_code="CA",
        flag_hints=[FlagType.POSTAL_UNRESOLVED, FlagType.MUNICIPALITY_UNRESOLVED],
        prior_search_log=[],
    )
    flag_types = {f.flag_type for f in out.flags}
    assert FlagType.POSTAL_UNRESOLVED in flag_types
    assert FlagType.MUNICIPALITY_UNRESOLVED in flag_types


def test_escalation_does_not_redo_prior_searches(mock_llm, mock_tavily):
    """Prior search results are passed in messages — escalator should not re-fire them."""
    from cleaning.escalation import EscalationAgent
    from cleaning.cache import WebSearchCache
    from cleaning.flags import FlagType
    from cleaning.types import SearchHit

    cache = WebSearchCache()
    mock_tavily.return_value = "fresh result"
    # Pre-populate cache with the prior query so a re-fire would be a HIT not a MISS.
    cache.put("M6H Toronto postal", "prior-result")
    mock_llm.messages_create.return_value = _mock_response(
        text='{"country": "Canada", "postal_code": "M6H 1E7", '
             '"municipality": "The Annex", "validation_notes": "ok"}'
    )
    esc = EscalationAgent(llm_client=mock_llm, web_cache=cache,
                          tools=[{"name": "web_search"}])
    prior = [SearchHit("M6H Toronto postal", "prior-result")]
    esc.investigate(
        record={"id": 1, "country": "Canada"},
        country_code="CA", flag_hints=[FlagType.POSTAL_UNRESOLVED],
        prior_search_log=prior,
    )
    # Verify the prior search log was passed into the system or message context.
    # We check by inspecting what was sent to messages_create.
    args, kwargs = mock_llm.messages_create.call_args
    messages_sent = kwargs["messages"]
    flat_text = str(messages_sent)
    assert "M6H Toronto postal" in flat_text or "prior-result" in flat_text
