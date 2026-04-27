"""Shared dataclasses for the cleaning subpackage.

These types form the data contracts between CleaningAgent, EscalationAgent,
and the orchestrator. Keeping them here prevents cyclic imports.
"""
from dataclasses import dataclass, field
from typing import Any

from cleaning.flags import Flag


@dataclass
class SearchHit:
    """One web search executed during research, preserved for escalation reuse."""
    query: str
    result: str


@dataclass
class CleaningOutput:
    """Per-record output from CleaningAgent.process() or EscalationAgent.investigate()."""
    cleaned_record: dict[str, Any]
    flags: list[Flag] = field(default_factory=list)
    search_log: list[SearchHit] = field(default_factory=list)


@dataclass
class CleaningRunReport:
    """End-of-run summary from run_cleaning_workflow()."""
    records_processed: int
    cleaned_count: int
    flagged_count: int
    flags_by_type: dict[str, int]
    cache_stats: dict[str, int]
    timing: dict[str, float]
    flag_summary: list[dict]
    errors: list[dict]
    summary_text: str
