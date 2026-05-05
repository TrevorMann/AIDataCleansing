"""Orchestrator v2: Agent team + skill registry based cleaning pipeline."""

import logging
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass

from skills.registry import SkillRegistry
from skills.agent import BaseAgent


class BatchBudget:
    """Per-batch query budget for expensive operations (Tavily, LLM calls)."""

    def __init__(self, max_queries: int = 100):
        self.max_queries = max_queries
        self.remaining = max_queries
        self.spent = 0

    def take(self, n: int = 1) -> bool:
        """Take n queries from budget. Returns False if exhausted."""
        if self.remaining < n:
            return False
        self.remaining -= n
        self.spent += n
        return True

    def summary(self) -> str:
        return f"Budget: {self.spent}/{self.max_queries} used, {self.remaining} remaining"


@dataclass
class CleaningRunReport:
    """Simple report of cleaning run results."""
    records_processed: int
    cleaned_count: int
    flagged_count: int
    flags_by_type: Dict
    cache_stats: Dict
    timing: Dict
    flag_summary: list
    errors: list
    summary_text: str


logger = logging.getLogger(__name__)


class OrchestrationTeam:
    """Agent team for cleaning pipeline."""

    def __init__(self, registry: SkillRegistry):
        """Initialize agent team with skills from registry.

        Args:
            registry: Loaded SkillRegistry for domain
        """
        self.registry = registry

        # Create specialized agents
        fuzzy_skill = registry.get("fuzzy_matcher")
        tools = {"fuzzy_matcher": fuzzy_skill} if fuzzy_skill else {}

        self.address_cleaner = BaseAgent(
            name="AddressCleaningAgent",
            skills=["spell_checker", "address_standardizer", "fuzzy_matcher"],
            registry=registry,
            tools=tools,
        )

        self.geographic_validator = BaseAgent(
            name="GeographicAlignmentAgent",
            skills=["municipality_authority", "geographic_validator"],
            registry=registry,
        )

        self.quality_triage = BaseAgent(
            name="QualityTriageAgent",
            skills=["data_quality_triage"],
            registry=registry,
        )

    def process_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Process record through agent team pipeline.

        Args:
            record: Raw record to process

        Returns:
            Processed record with decisions log
        """
        decisions_log = []

        # Stage 1: Address Cleaning (spelling, standardization, fuzzy matching)
        record = self.address_cleaner.execute(record)
        if "_decisions" in record:
            decisions_log.extend(record["_decisions"])
            del record["_decisions"]

        # Stage 2: Geographic Validation (municipality, boundary, coherence)
        record = self.geographic_validator.execute(record)
        if "_decisions" in record:
            decisions_log.extend(record["_decisions"])
            del record["_decisions"]

        # Stage 3: Quality Triage (done / review / unsalvageable)
        record = self.quality_triage.execute(record)
        if "_decisions" in record:
            decisions_log.extend(record["_decisions"])
            del record["_decisions"]

        # Attach decisions log for audit trail
        if decisions_log:
            record["_agent_decisions"] = decisions_log

        return record


def run_cleaning_workflow_v2(
    records: list,
    verbose: bool = False,
) -> CleaningRunReport:
    """Cleaning workflow using agent team + skill registry.

    Args:
        records: List of records to process
        verbose: Verbose logging

    Returns:
        CleaningRunReport with results and metrics
    """
    timing: Dict[str, float] = {}

    try:
        # Load skill registry once at startup
        t = time.time()
        registry = SkillRegistry.load("real_estate")
        timing["skill_registry_load"] = time.time() - t

        if verbose:
            print(f"Loaded skill registry: {registry}")
            print(f"Available skills: {', '.join(registry.list_skills())}")

        # Initialize agent team
        t = time.time()
        team = OrchestrationTeam(registry)
        timing["agent_team_init"] = time.time() - t

        if not records:
            return _empty_report(timing, "No records to process.")

        if verbose:
            print(f"\nProcessing {len(records)} records through agent team...")

        # Process records through agent team
        t = time.time()
        processed_records = []
        for i, record in enumerate(records):
            if verbose:
                print(f"  [{i+1}/{len(records)}] Processing record {record.get('id')}...", end=" ", flush=True)

            processed = team.process_record(record)
            processed_records.append(processed)

            if verbose:
                decisions = processed.get("_agent_decisions", [])
                print(f"({len(decisions)} decisions)")

        timing["agent_team_processing"] = time.time() - t

        saved = len(processed_records)
        errors = []

        summary_text = (
            f"Cleaned {saved}/{len(records)} records using agent team. "
            f"{len(errors)} errors. "
            f"Total: {sum(timing.values()):.2f}s."
        )

        return CleaningRunReport(
            records_processed=len(records),
            cleaned_count=saved,
            flagged_count=0,
            flags_by_type={},
            cache_stats={"hits": 0, "misses": 0, "pg_hits": 0, "queries_cached": 0},
            timing=timing,
            flag_summary=[],
            errors=errors,
            summary_text=summary_text,
        )

    except Exception as e:
        logger.error(f"Error in orchestration: {e}")
        return _empty_report(timing, f"Error: {str(e)}")




def _empty_report(timing: dict, message: str) -> CleaningRunReport:
    """Return empty report for when no records are found."""
    return CleaningRunReport(
        records_processed=0,
        cleaned_count=0,
        flagged_count=0,
        flags_by_type={},
        cache_stats={"hits": 0, "misses": 0, "pg_hits": 0, "queries_cached": 0},
        timing=timing,
        flag_summary=[],
        errors=[],
        summary_text=message,
    )
