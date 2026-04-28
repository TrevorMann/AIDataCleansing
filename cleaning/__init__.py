"""Public API for the cleaning subpackage.

See spec at docs/superpowers/specs/2026-04-27-data-cleaning-c-hybrid-refactor-design.md
"""
from cleaning.conversation import AdHocConversation
from cleaning.flags import Flag, FlagSeverity, FlagType
from cleaning.llm_client import Clients, LLMClient, build_clients
from cleaning.orchestrator import run_cleaning_workflow
from cleaning.types import CleaningOutput, CleaningRunReport, SearchHit

__all__ = [
    "run_cleaning_workflow", "build_clients", "Clients", "LLMClient",
    "CleaningOutput", "CleaningRunReport", "SearchHit",
    "Flag", "FlagSeverity", "FlagType",
    "AdHocConversation",
]
