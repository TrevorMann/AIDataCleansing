"""Per-record escalation sub-agent for hard cases.

Triggered by CleaningAgent when needs_escalation() returns non-empty for a record.
Receives the parent's prior_search_log so it does not redo searches.
Returns an updated CleaningOutput with flags. Does NOT touch the DB.
See spec §5.2.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from cleaning.cache import WebSearchCache
from cleaning.flags import Flag, FlagSeverity, FlagType
from cleaning.llm_client import LLMClient
from cleaning.types import CleaningOutput, SearchHit


logger = logging.getLogger(__name__)


# These are the only flag types that needs_escalation() can evaluate.
# Hints outside this set cannot be detected as resolved or unresolved by that function.
_NEEDS_ESCALATION_DETECTABLE = frozenset({
    FlagType.UNKNOWN_COUNTRY,
    FlagType.POSTAL_UNRESOLVED,
    FlagType.POSTAL_AMBIGUOUS,
    FlagType.MUNICIPALITY_UNRESOLVED,
    FlagType.LOW_CONFIDENCE_RESEARCH,
})


_SYSTEM_PROMPT = """You are an escalation specialist for a real-estate data cleaning system.
You receive ONE record that the country agent could not fully resolve, plus the
list of issues to investigate and a transcript of prior web searches.

Your job:
- Resolve the issues if you can. Use web_search ONLY for queries not in the prior log.
- If you cannot resolve, return your best guess and explicitly state the confidence.
- Return ONLY a JSON object with the fields: country, postal_code, municipality, validation_notes.
"""


class EscalationAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        web_cache: WebSearchCache,
        tools: list[dict],
        max_rounds: int = 10,
    ):
        self.llm = llm_client
        self.cache = web_cache
        self.tools = tools
        self.max_rounds = max_rounds

    def investigate(
        self,
        record: dict,
        country_code: Optional[str],
        flag_hints: list[FlagType],
        prior_search_log: list[SearchHit],
    ) -> CleaningOutput:
        prompt = self._build_prompt(record, country_code, flag_hints, prior_search_log)
        messages: list[dict] = [{"role": "user", "content": prompt}]

        for _ in range(self.max_rounds):
            resp = self.llm.messages_create(
                system=_SYSTEM_PROMPT, messages=messages, tools=self.tools,
            )
            tool_calls = [b for b in resp.content
                          if hasattr(b, "type") and b.type == "tool_use"]
            if not tool_calls:
                final_text = next((b.text for b in resp.content if hasattr(b, "text")), "")
                return self._build_output(record, final_text, flag_hints)
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for tc in tool_calls:
                result = self.cache.web_search_cached(tc.input.get("query", ""))
                tool_results.append({"type": "tool_result", "tool_use_id": tc.id,
                                     "content": result})
            messages.append({"role": "user", "content": tool_results})

        logger.warning("EscalationAgent: max_rounds reached on record %s", record.get("id"))
        return self._build_output(record, "", flag_hints)

    def _build_prompt(self, record, country_code, flag_hints, prior_search_log):
        prior = "\n".join(f"  query: {s.query}\n  result snippet: {s.result[:200]}..."
                          for s in prior_search_log) or "(none)"
        return (
            f"COUNTRY (may be unknown): {country_code}\n\n"
            f"RECORD:\n{json.dumps(record, indent=2)}\n\n"
            f"ISSUES TO RESOLVE: {[ft.value for ft in flag_hints]}\n\n"
            f"PRIOR SEARCHES (do NOT repeat these queries):\n{prior}\n\n"
            f"Resolve the issues. Return ONLY the JSON object."
        )

    def _build_output(
        self, record: dict, final_text: str, flag_hints: list[FlagType],
    ) -> CleaningOutput:
        merged = dict(record)
        text = final_text.strip()
        if text.startswith("```"):
            text = "\n".join(
                line for line in text.splitlines()
                if not line.strip().startswith("```")
            ).strip()
        try:
            parsed = json.loads(text)
            for k in ("country", "postal_code", "municipality", "validation_notes"):
                if parsed.get(k):
                    merged[k] = parsed[k]
        except (json.JSONDecodeError, ValueError):
            logger.warning("EscalationAgent: could not parse JSON: %r", final_text[:200])

        flags = self._build_flags(merged, flag_hints)
        return CleaningOutput(cleaned_record=merged, flags=flags)

    def _build_flags(self, merged: dict, flag_hints: list[FlagType]) -> list[Flag]:
        """Decide which hint-flags survived (still unresolved) vs which were resolved."""
        from cleaning.agent import needs_escalation
        survivors = set(needs_escalation(CleaningOutput(cleaned_record=merged)))
        flags: list[Flag] = []
        any_resolved = False
        for hint in flag_hints:
            if hint not in _NEEDS_ESCALATION_DETECTABLE or hint in survivors:
                # Undetectable by needs_escalation, or still present → not resolved
                flags.append(Flag(
                    flag_type=hint,
                    severity=FlagSeverity.NEEDS_REVIEW,
                    reason=f"escalation could not resolve {hint.value}",
                    raised_by="escalator",
                ))
            else:
                any_resolved = True
        if any_resolved:
            flags.append(Flag(
                flag_type=FlagType.RESOLVED_AFTER_ESCALATION,
                severity=FlagSeverity.INFO,
                reason="record resolved by escalation",
                raised_by="escalator",
            ))
        return flags
