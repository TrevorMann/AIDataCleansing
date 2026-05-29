"""Public API for the cleaning subpackage.

See spec at docs/superpowers/specs/2026-04-27-data-cleaning-c-hybrid-refactor-design.md
"""
from cleaning.conversation import AdHocConversation
from cleaning.flags import Flag, FlagSeverity, FlagType
from cleaning.llm_client import Clients, LLMClient, build_clients
from cleaning.types import CleaningOutput, CleaningRunReport, SearchHit

# NOTE: the v1 orchestrator (cleaning/orchestrator.py, run_cleaning_workflow) is
# retired. It depends on the deleted pre_cleaner module (legacy hardcoded country
# logic) and is superseded by cleaning.orchestrator_v2 (run_cleaning_workflow_v2 /
# OrchestrationTeam), which is what the CLI and pipeline use. The file remains on
# disk as dead code but is intentionally not imported/exported here.

__all__ = [
    "build_clients", "Clients", "LLMClient",
    "CleaningOutput", "CleaningRunReport", "SearchHit",
    "Flag", "FlagSeverity", "FlagType",
    "AdHocConversation",
]
