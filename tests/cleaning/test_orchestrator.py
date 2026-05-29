"""Tests for cleaning.orchestrator helpers + run_cleaning_workflow.

Helper tests added in Task 11; workflow tests added in Task 12.
"""
from unittest.mock import MagicMock
import pytest

# The v1 orchestrator is retired (depends on the deleted pre_cleaner module and is
# superseded by orchestrator_v2). These tests cover dead code — skipped, not deleted.
pytestmark = pytest.mark.skip(reason="v1 orchestrator retired; superseded by orchestrator_v2")


def test_detect_country_filter_explicit_override():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("anything", override="USA") == "USA"


def test_detect_country_filter_keyword_canada():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("CLEAN canadian data") == "CA"


def test_detect_country_filter_ambiguous_returns_none():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("CLEAN all uncleaned data") is None


def test_detect_country_filter_north_american_returns_none_for_per_record_routing():
    from cleaning.orchestrator import detect_country_filter
    assert detect_country_filter("CLEAN north american data") is None


def test_interpret_query_extracts_country_and_scope():
    from cleaning.orchestrator import interpret_query
    f = interpret_query("CLEAN japanese data")
    assert f.get("country") == "JP"
    f2 = interpret_query("CLEAN all uncleaned data")
    assert f2.get("scope") == "all_uncleaned"


def test_group_by_country_uses_pre_cleaner_canonical_code():
    from cleaning.orchestrator import group_by_country
    records = [
        {"id": 1, "country": "Canada"},
        {"id": 2, "country": "United States"},
        {"id": 3, "country": "CA"},
        {"id": 4, "country": ""},
        {"id": 5, "country": "Atlantis"},  # unknown
    ]
    g = group_by_country(records)
    assert {1, 3} == {r["id"] for r in g["CA"]}
    assert {2} == {r["id"] for r in g["USA"]}
    # records with no resolvable country code go under None
    assert {4, 5} == {r["id"] for r in g[None]}


def test_merge_results_combines_pre_cleaned_with_agent_output():
    from cleaning.orchestrator import merge_results
    from cleaning.types import CleaningOutput
    pre = [{"id": 1, "name": "John", "_pre_clean_changes": ["name capitalized"]}]
    outs = [CleaningOutput(cleaned_record={"id": 1, "postal_code": "M6H 1E7",
                                            "municipality": "The Annex",
                                            "validation_notes": "HIGH"})]
    merged = merge_results(pre, outs)
    assert merged[0]["raw_data_id"] == 1
    assert "Pre-cleaned" in merged[0]["validation_notes"]
    assert merged[0]["postal_code"] == "M6H 1E7"


def test_fetch_records_filters_by_country(tmp_db):
    from db_helpers import insert_raw_data
    from cleaning.orchestrator import fetch_records
    insert_raw_data(tmp_db, name="alice", country="Canada")
    insert_raw_data(tmp_db, name="bob", country="United States")
    insert_raw_data(tmp_db, name="carol", country="CA")
    canadian = fetch_records(tmp_db, filters={"country": "CA"})
    assert {r["name"] for r in canadian} == {"alice", "carol"}


def test_fetch_records_excludes_already_cleaned(tmp_db):
    """scope=all_uncleaned must not return records that already have a cleaned row."""
    import sqlite3
    from db_helpers import insert_raw_data
    from cleaning.orchestrator import fetch_records
    raw_id_1 = insert_raw_data(tmp_db, name="already_done", country="CA")
    insert_raw_data(tmp_db, name="still_dirty", country="CA")
    # simulate a pre-existing cleaned_data row for record 1
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO cleaned_data (raw_data_id, name, country) VALUES (?, ?, ?)",
        (raw_id_1, "already_done", "Canada"),
    )
    conn.commit()
    conn.close()
    result = fetch_records(tmp_db, filters={"scope": "all_uncleaned"})
    assert all(r["name"] != "already_done" for r in result)
    assert any(r["name"] == "still_dirty" for r in result)


# ---- Workflow integration tests ----


def test_run_cleaning_workflow_end_to_end_mixed_batch(tmp_db, mock_tavily):
    """Mixed batch: 2 CA + 1 USA + 1 unknown country.
    Mocks LLM to return canned tables. Verifies records persisted, flags raised.
    """
    from db_helpers import insert_raw_data, query_flags
    from cleaning.llm_client import Clients, LLMClient
    from cleaning.orchestrator import run_cleaning_workflow

    insert_raw_data(tmp_db, name="alice", country="Canada", postal_code="M6H 1E7",
                    address="25 Muir Ave", city="Toronto", state_province="Ontario",
                    municipality="")
    insert_raw_data(tmp_db, name="bob", country="CA", postal_code="V6B 2W9",
                    address="100 Granville", city="Vancouver", state_province="BC",
                    municipality="")
    insert_raw_data(tmp_db, name="carol", country="USA", postal_code="10025",
                    address="123 W 95th St", city="New York", state_province="NY",
                    municipality="")
    insert_raw_data(tmp_db, name="diana", country="", postal_code="???",
                    address="?", city="?", state_province="?",
                    municipality="")

    standard_resp_text = (
        "| ID | Postal Code | Municipality | Validation Notes |\n"
        "| 1 | M6H 1E7 | The Annex | HIGH |\n"
        "| 2 | V6B 2W9 | Yaletown | HIGH |\n"
        "| 3 | 10025 | Upper West Side | HIGH |"
    )
    text_block = MagicMock(); text_block.text = standard_resp_text
    del text_block.name
    text_block.type = "text"
    standard_resp = MagicMock(); standard_resp.content = [text_block]
    standard_resp.stop_reason = "end_turn"

    deep_resp_text = '{"country": "Canada", "postal_code": "M6H 1E7", ' \
                     '"municipality": "The Annex", "validation_notes": "resolved"}'
    deep_block = MagicMock(); deep_block.text = deep_resp_text
    del deep_block.name
    deep_block.type = "text"
    deep_resp = MagicMock(); deep_resp.content = [deep_block]
    deep_resp.stop_reason = "end_turn"

    fast_client = LLMClient(sdk=MagicMock(), model="fast",
                            supports_cache_control=False, base_url=None)
    standard_client = LLMClient(sdk=MagicMock(), model="std",
                                 supports_cache_control=False, base_url=None)
    standard_client.sdk.messages.create.return_value = standard_resp
    deep_client = LLMClient(sdk=MagicMock(), model="deep",
                             supports_cache_control=False, base_url=None)
    deep_client.sdk.messages.create.return_value = deep_resp
    clients = Clients(fast=fast_client, standard=standard_client, deep=deep_client)

    report = run_cleaning_workflow(
        "CLEAN all uncleaned data", db_path=tmp_db, clients=clients,
    )

    assert report.records_processed == 4
    assert report.cleaned_count == 4
    # diana's unknown country triggers UNKNOWN_COUNTRY → escalator resolves it
    flags = query_flags(tmp_db, only_unresolved=False)
    flag_types = {f["flag_type"] for f in flags}
    assert "resolved_after_escalation" in flag_types or "unknown_country" in flag_types


def test_run_cleaning_workflow_no_records_returns_zero_report(tmp_db):
    from cleaning.llm_client import Clients, LLMClient
    from cleaning.orchestrator import run_cleaning_workflow

    fake = LLMClient(sdk=MagicMock(), model="m",
                     supports_cache_control=False, base_url=None)
    clients = Clients(fast=fake, standard=fake, deep=fake)
    report = run_cleaning_workflow("CLEAN canadian data", db_path=tmp_db, clients=clients)
    assert report.records_processed == 0
    assert "No records" in report.summary_text
