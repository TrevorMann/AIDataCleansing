"""Smoke tests for cleaning.types dataclasses."""
from cleaning.types import SearchHit, CleaningOutput, CleaningRunReport


def test_search_hit_construction():
    hit = SearchHit(query="M6H Toronto postal", result="...long result...")
    assert hit.query == "M6H Toronto postal"


def test_cleaning_output_defaults():
    out = CleaningOutput(cleaned_record={"id": 1})
    assert out.cleaned_record == {"id": 1}
    assert out.flags == []
    assert out.search_log == []


def test_cleaning_run_report_construction():
    rep = CleaningRunReport(
        records_processed=10, cleaned_count=8, flagged_count=2,
        flags_by_type={"postal_unresolved": 1, "municipality_unresolved": 1},
        cache_stats={"hits": 5, "misses": 3, "queries_cached": 3},
        timing={"interpret": 0.1, "fetch": 0.05, "pre_clean": 0.2,
                "research": 8.0, "persist": 0.3},
        flag_summary=[],
        errors=[],
        summary_text="ok",
    )
    assert rep.records_processed == 10
    assert "postal_unresolved" in rep.flags_by_type
