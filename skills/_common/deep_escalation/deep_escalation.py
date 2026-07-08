"""Deep-tier escalation skill — last line of the escalation ladder.

Wraps cleaning.escalation.EscalationAgent (deep model tier, e.g. Sonnet) as a
v2 skill. Fires only for records still routed needs_review after the planner
phase, reusing the record's prior web-search evidence so searches are not
repeated. Budget-gated by the orchestrator (cost: high).
"""

from typing import Any, Dict, List, Optional

from skills.base import BaseSkill

_WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search the web to verify or resolve record fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}


class DeepEscalation(BaseSkill):
    """Escalate a stuck record to the deep LLM tier for investigation."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.tier = self.config.get("tier", "deep")
        self.max_rounds = self.config.get("max_rounds", 10)
        self.web_cache = self.config.get("web_cache")
        self._escalator = self.config.get("escalator")  # injectable for tests

    def _get_escalator(self):
        if self._escalator is None:
            from cleaning.cache import WebSearchCache
            from cleaning.escalation import EscalationAgent
            from cleaning.llm_client import build_client_for_tier

            self._escalator = EscalationAgent(
                llm_client=build_client_for_tier(self.tier),
                web_cache=self.web_cache or WebSearchCache(),
                tools=[_WEB_SEARCH_TOOL],
                max_rounds=self.max_rounds,
            )
        return self._escalator

    def run(self, record: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        if record.get("_triage_route") != "needs_review":
            return record

        flag_hints = self._flag_hints(record)
        prior_log = self._prior_search_log(record)

        try:
            out = self._get_escalator().investigate(
                record={k: v for k, v in record.items() if not k.startswith("_")},
                country_code=record.get("country"),
                flag_hints=flag_hints,
                prior_search_log=prior_log,
            )
        except Exception as e:
            self.log_decision(
                "Deep escalation failed",
                f"{type(e).__name__}: {str(e)[:120]}",
                confidence=0.0,
            )
            return record

        changed = {}
        for k, v in out.cleaned_record.items():
            if v and record.get(k) != v:
                changed[k] = v
        record.update(changed)
        record["_escalation_flags"] = [f.flag_type.value for f in out.flags]

        resolved = any(
            f.flag_type.value == "resolved_after_escalation" for f in out.flags
        )
        self.log_decision(
            f"Deep escalation {'resolved' if resolved else 'did not resolve'} record",
            f"changed fields: {sorted(changed) or 'none'}; "
            f"flags: {record['_escalation_flags']}",
            confidence=0.85 if resolved else 0.4,
        )
        return record

    def _flag_hints(self, record: dict) -> list:
        """Map v2 gap hints onto FlagType hints the escalator understands."""
        from cleaning.flags import FlagType

        valid = {ft.value: ft for ft in FlagType}
        hints = []
        for gap in record.get("_gap_hints", []):
            if gap in valid:
                hints.append(valid[gap])
        if not hints:
            hints.append(FlagType.LOW_CONFIDENCE_RESEARCH)
        return hints

    def _prior_search_log(self, record: dict) -> list:
        from cleaning.types import SearchHit

        return [
            SearchHit(query=e.get("query", ""), result=e.get("snippet") or "")
            for e in record.get("_web_search_evidence", [])
            if e.get("query")
        ]
