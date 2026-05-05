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
    """Multi-phase cleaning pipeline: deterministic → triage → AI plan → enrichment → re-triage."""

    def __init__(self, registry: SkillRegistry, batch_budget: Optional["BatchBudget"] = None):
        """Initialize agent team with skills from registry.

        Args:
            registry: Loaded SkillRegistry for domain
            batch_budget: Optional per-batch query budget for web search / LLM calls
        """
        self.registry = registry
        self.batch_budget = batch_budget
        self.planner = registry.get("skill_planner")
        self.triage_skill = registry.get("data_quality_triage")

    def _collect_decisions(self, record: dict, log: list):
        if "_decisions" in record:
            log.extend(record["_decisions"])
            del record["_decisions"]

    def process_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Multi-phase pipeline.

        Phase 1: Deterministic skills (cost=low), always run.
        Phase 2: Initial triage — route early exits.
        Phase 3: AI Planner selects medium/high-cost skills for ambiguous records.
        Phase 4: Run planned skills with budget enforcement.
        Phase 5: Re-triage with enriched record.
        """
        decisions_log = []
        fuzzy_skill = self.registry.get("fuzzy_matcher")
        base_tools = {"fuzzy_matcher": fuzzy_skill} if fuzzy_skill else {}

        # Phase 1: Deterministic skills (cost=low, no DB required)
        for skill_name in self.registry.skills_by_cost("low"):
            skill = self.registry.get(skill_name)
            if skill:
                record = skill.run(record, base_tools)
                self._collect_decisions(record, decisions_log)

        # Phase 2: Initial triage
        if self.triage_skill:
            record = self.triage_skill.run(record)
            self._collect_decisions(record, decisions_log)

        route = record.get("_triage_route")
        if route in ("done", "unsalvageable"):
            if decisions_log:
                record["_agent_decisions"] = decisions_log
            return record

        # Phase 3: AI Planner (optional — only if registered and record is ambiguous)
        planned_skills = []
        if self.planner:
            record = self.planner.run(record, tools={"registry": self.registry})
            self._collect_decisions(record, decisions_log)
            planned_skills = record.get("_planned_skills", [])

        if not planned_skills:
            # Fallback: run all medium-cost skills in dep order
            planned_skills = self.registry.skills_by_cost("medium")

        # Phase 4: Run planned skills (skip cost=low already done, skip triage/planner)
        skip = set(self.registry.skills_by_cost("low")) | {"data_quality_triage", "skill_planner"}
        for skill_name in planned_skills:
            if skill_name in skip:
                continue
            skill = self.registry.get(skill_name)
            meta = self.registry.get_metadata(skill_name) or {}
            if not skill:
                continue

            # Budget enforcement for high-cost skills
            if meta.get("cost") == "high" and self.batch_budget:
                if not self.batch_budget.take():
                    decisions_log.append({
                        "skill": "OrchestrationTeam",
                        "decision": f"Skipped {skill_name} — batch budget exhausted",
                        "reason": self.batch_budget.summary(),
                        "confidence": 0.0,
                    })
                    continue

            tools = dict(base_tools)
            if self.batch_budget:
                tools["batch_budget"] = self.batch_budget

            record = skill.run(record, tools)
            self._collect_decisions(record, decisions_log)

        # Phase 5: Re-triage with enriched evidence
        if self.triage_skill:
            record = self.triage_skill.run(record)
            self._collect_decisions(record, decisions_log)

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
