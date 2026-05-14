"""Orchestrator v2: Agent team + skill registry based cleaning pipeline."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from skills.registry import SkillRegistry


class BatchBudget:
    """Per-batch query budget for expensive operations (Tavily, LLM calls)."""

    def __init__(self, max_queries: int = 100):
        self.max_queries = max_queries
        self.remaining = max_queries
        self.spent = 0

    def take(self, n: int = 1) -> bool:
        if self.remaining < n:
            return False
        self.remaining -= n
        self.spent += n
        return True

    def summary(self) -> str:
        return f"Budget: {self.spent}/{self.max_queries} used, {self.remaining} remaining"


@dataclass
class CleaningRunReport:
    records_processed: int
    cleaned_count: int
    flagged_count: int
    flags_by_type: Dict
    cache_stats: Dict
    timing: Dict
    flag_summary: list
    errors: list
    summary_text: str
    audit_log: List[Dict] = field(default_factory=list)


logger = logging.getLogger(__name__)


class OrchestrationTeam:
    """Multi-phase cleaning pipeline: parallel deterministic → triage → domain skills → web search → (last resort) LLM planner."""

    def __init__(self, registry: SkillRegistry, batch_budget: Optional[BatchBudget] = None):
        self.registry = registry
        self.batch_budget = batch_budget
        self.planner = registry.get("skill_planner")
        self.triage_skill = registry.get("data_quality_triage")
        self._warn_annotation_gaps()

    def _warn_annotation_gaps(self) -> None:
        """Warn once at session start if domain columns lack annotations."""
        conn = self.registry.runtime.get("pg_conn") if hasattr(self.registry, "runtime") else None
        domain = getattr(self.registry, "domain", None)
        if not conn or not domain:
            return
        try:
            from services.metadata_annotation import MetadataAnnotationService
            gaps = MetadataAnnotationService(llm_client=None).list_gaps(domain, conn)
            if gaps:
                logger.warning(
                    "%d column(s) in '%s' have no annotations. "
                    "Run: python scripts/annotate_domain.py --domain %s",
                    len(gaps), domain, domain,
                )
        except Exception:
            pass

    def _run_skill(self, skill, record: dict, tools: dict = None) -> Tuple[dict, List]:
        """Run a skill, collect audit, strip _decisions from record."""
        skill.clear_audit()
        result = skill.run(dict(record), tools or {})
        result.pop("_decisions", None)   # backward compat strip during transition
        return result, skill.get_audit()

    def _phase1_skills(self) -> List:
        """Return skills with phase==1, excluding record_linker (batch-only).

        Each returned instance is distinct (one per named skill in the registry).
        Parallel execution in process_record depends on this — do not add aliases
        that return the same instance under two names.
        """
        skills = []
        for name, meta in self.registry.metadata.items():
            if meta.get("phase") != 1:
                continue
            skill = self.registry.get(name)
            if skill is None or skill.__class__.__name__ == "RecordLinker":
                continue
            skills.append(skill)
        return skills

    def process_record(self, record: Dict[str, Any]) -> Tuple[Dict[str, Any], List]:
        """Process a single record through the pipeline.

        Returns (cleaned_record, audit_entries).
        Audit entries are never placed into the record.
        """
        audit_log = []

        # Phase 1: Deterministic skills (phase=1) — run in parallel
        phase1 = self._phase1_skills()
        if phase1:
            merged = dict(record)
            with ThreadPoolExecutor(max_workers=len(phase1)) as executor:
                futures = {
                    executor.submit(self._run_skill, skill, record): skill
                    for skill in phase1
                }
                for future in as_completed(futures):
                    result, entries = future.result()
                    audit_log.extend(entries)
                    # Phase-1 skills operate on disjoint field sets (spell_checker: text_fields,
                    # address_standardizer: address_fields). Last-writer-wins on overlap is the
                    # fallback, but overlapping configs in skills.yaml would cause silent data loss.
                    # Only apply fields that changed from the original record to avoid
                    # parallel-merge races (skills each get a copy of the original).
                    for k, v in result.items():
                        if k not in record or record[k] != v:
                            merged[k] = v
            record = merged

        # Phase 2: Initial triage
        if self.triage_skill:
            record, entries = self._run_skill(self.triage_skill, record)
            audit_log.extend(entries)

        route = record.get("_triage_route")
        if route in ("done", "unsalvageable"):
            return record, audit_log

        # Phase 3: Domain skills (phase=2, in dependency order)
        phase2_names = [
            name for name, meta in self.registry.metadata.items()
            if meta.get("phase") == 2
        ]
        for skill_name in self.registry.topological_sort(phase2_names):
            skill = self.registry.get(skill_name)
            if not skill:
                continue
            meta = self.registry.get_metadata(skill_name) or {}
            if meta.get("cost") == "high" and self.batch_budget:
                if not self.batch_budget.take():
                    audit_log.append({
                        "skill": "OrchestrationTeam",
                        "decision": f"Skipped {skill_name} — budget exhausted",
                        "reason": self.batch_budget.summary(),
                        "confidence": 0.0,
                    })
                    continue
            tools = {"batch_budget": self.batch_budget} if self.batch_budget else {}
            record, entries = self._run_skill(skill, record, tools)
            audit_log.extend(entries)

        # Phase 4: Web search enrichment (phase=3)
        web_enricher = self.registry.get("web_search_enricher")
        if web_enricher and record.get("_triage_route") == "needs_review":
            tools = {}
            if self.batch_budget:
                tools["batch_budget"] = self.batch_budget
            record, entries = self._run_skill(web_enricher, record, tools)
            audit_log.extend(entries)

        # Re-triage with enriched evidence
        if self.triage_skill:
            record, entries = self._run_skill(self.triage_skill, record)
            audit_log.extend(entries)

        route = record.get("_triage_route")
        if route in ("done", "unsalvageable"):
            return record, audit_log

        # Phase 5: LLM Planner — LAST RESORT only
        if self.planner:
            record, entries = self._run_skill(
                self.planner, record, tools={"registry": self.registry}
            )
            audit_log.extend(entries)
            planned_skills = record.get("_planned_skills", [])
            skip = {"data_quality_triage", "skill_planner"}
            for skill_name in planned_skills:
                if skill_name in skip:
                    continue
                skill = self.registry.get(skill_name)
                if skill:
                    record, entries = self._run_skill(skill, record)
                    audit_log.extend(entries)

        # Final triage
        if self.triage_skill:
            record, entries = self._run_skill(self.triage_skill, record)
            audit_log.extend(entries)

        return record, audit_log

    def process_batch(self, records: List[Dict[str, Any]]) -> Tuple[List[Dict], List]:
        """Process a batch. Runs record_linker.link_batch() after per-record pass."""
        all_audit = []
        processed = []

        for record in records:
            cleaned, audit = self.process_record(record)
            processed.append(cleaned)
            all_audit.extend(audit)

        # Batch record linkage — transitive group assignment across all records
        record_linker = self.registry.get("record_linker")
        if record_linker and hasattr(record_linker, "link_batch"):
            # link_batch assigns _group_id to each record but generates no audit entries —
            # the linkage outcome is readable from record["_group_id"].
            processed = record_linker.link_batch(processed)

        return processed, all_audit


def run_cleaning_workflow_v2(
    records: list,
    verbose: bool = False,
    domain: str = "real_estate",
) -> CleaningRunReport:
    timing: Dict[str, float] = {}

    try:
        t = time.time()
        registry = SkillRegistry.load(domain)
        timing["skill_registry_load"] = time.time() - t

        if verbose:
            print(f"Loaded skill registry: {registry}")

        t = time.time()
        team = OrchestrationTeam(registry)
        timing["agent_team_init"] = time.time() - t

        if not records:
            return _empty_report(timing, "No records to process.")

        t = time.time()
        processed_records, audit_log = team.process_batch(records)
        timing["agent_team_processing"] = time.time() - t

        if verbose:
            for i, r in enumerate(processed_records):
                print(f"  [{i+1}/{len(records)}] id={r.get('id')} route={r.get('_triage_route')}")

        summary_text = (
            f"Cleaned {len(processed_records)}/{len(records)} records. "
            f"{len(audit_log)} audit entries. "
            f"Total: {sum(timing.values()):.2f}s."
        )

        return CleaningRunReport(
            records_processed=len(records),
            cleaned_count=len(processed_records),
            flagged_count=0,
            flags_by_type={},
            cache_stats={"hits": 0, "misses": 0, "pg_hits": 0, "queries_cached": 0},
            timing=timing,
            flag_summary=[],
            errors=[],
            summary_text=summary_text,
            audit_log=audit_log,
        )

    except Exception as e:
        logger.error(f"Error in orchestration: {e}")
        return _empty_report(timing, f"Error: {str(e)}")


def _empty_report(timing: dict, message: str) -> CleaningRunReport:
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
