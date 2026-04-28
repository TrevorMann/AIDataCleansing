"""Per-country research agent + escalation predicate.

CleaningAgent class is added in Task 9 of the implementation plan.
"""
from __future__ import annotations

from cleaning.flags import FlagType
from cleaning.types import CleaningOutput


_VALID_COUNTRIES_FULL = {"CANADA", "UNITED STATES", "NETHERLANDS", "MEXICO", "JAPAN"}
_VALID_COUNTRIES_CODE = {"CA", "USA", "NL", "MX", "JP"}


def needs_escalation(output: CleaningOutput) -> list[FlagType]:
    """Decide which (if any) flags should trigger an escalation pass for this record.

    Pure function over the record + validation_notes. Returns a list of FlagType —
    empty means the record is fully resolved and does not need escalation.
    """
    rec = output.cleaned_record
    flags: list[FlagType] = []

    country = (rec.get("country") or "").strip()
    if (not country
        or country.upper() not in _VALID_COUNTRIES_FULL
           and country.upper() not in _VALID_COUNTRIES_CODE):
        flags.append(FlagType.UNKNOWN_COUNTRY)

    postal = (rec.get("postal_code") or "").strip()
    if not postal or postal.upper() == "N/A":
        flags.append(FlagType.POSTAL_UNRESOLVED)
    elif postal.endswith("?"):
        flags.append(FlagType.POSTAL_AMBIGUOUS)

    muni = (rec.get("municipality") or "").strip()
    if not muni or muni.upper() == "N/A":
        flags.append(FlagType.MUNICIPALITY_UNRESOLVED)

    notes = (rec.get("validation_notes") or "").upper()
    if "LOW" in notes and "CONFIDENCE" in notes:
        flags.append(FlagType.LOW_CONFIDENCE_RESEARCH)

    # TODO: add CROSS_REGION_MISMATCH detection (e.g. Canadian postal first letter
    # doesn't match province) when a postal-pattern library is available. The
    # FlagType value is already defined; implement as a follow-up task once a
    # lightweight CA/USA/NL/MX/JP postal-format validator is chosen.

    return flags


import logging
import re
from typing import TYPE_CHECKING, Callable

from cleaning.cache import WebSearchCache
from cleaning.flags import Flag, FlagSeverity
from cleaning.llm_client import LLMClient
from cleaning.types import CleaningOutput, SearchHit

if TYPE_CHECKING:
    from cleaning.escalation import EscalationAgent


logger = logging.getLogger(__name__)


class CleaningAgent:
    """Country-fixed research agent. See spec §5.1 and migration spec §5.

    Migration boundary invariants enforced here:
      1. country_code is fixed at construction; .process() never inspects records to
         decide "what country am I serving?".
      2. messages, _search_log, and counters are per-instance; nothing shared.
      3. .process() returns CleaningOutputs; never writes to the DB.
    """

    def __init__(
        self,
        country_code: str,
        system_prompt: str,
        research_prompt_builder: Callable[[str, str], str],
        tools: list[dict],
        llm_client: LLMClient,
        web_cache: WebSearchCache,
        escalator: "EscalationAgent",
        max_rounds: int = 20,
    ):
        self.country_code = country_code
        self.system_prompt = system_prompt
        self.research_prompt_builder = research_prompt_builder
        self.tools = tools
        self.llm = llm_client
        self.cache = web_cache
        self.escalator = escalator
        self.max_rounds = max_rounds
        self._search_log: list[SearchHit] = []

    # ------------------------------------------------------------------ public

    def process(self, records: list[dict]) -> list[CleaningOutput]:
        """Run the research loop for one country's batch, escalate hard cases."""
        if not records:
            return []

        self._search_log = []  # reset per invocation; each batch gets its own log
        research_table = self._format_research_batch(records)
        prompt = self.research_prompt_builder(self.country_code, research_table)
        raw_response = self._run_research_loop(prompt)
        parsed = self._parse_research_table(raw_response)

        outputs: list[CleaningOutput] = []
        for rec in records:
            merged = dict(rec)
            update = parsed.get(rec["id"], {})
            for k in ("postal_code", "municipality", "validation_notes"):
                if update.get(k):
                    merged[k] = update[k]
            out = CleaningOutput(
                cleaned_record=merged,
                flags=[],
                search_log=list(self._search_log),
            )
            flag_hints = needs_escalation(out)
            if flag_hints:
                escalated = self.escalator.investigate(
                    record=merged,
                    country_code=self.country_code,
                    flag_hints=flag_hints,
                    prior_search_log=list(self._search_log),
                )
                if escalated is not None:
                    out = escalated
            outputs.append(out)
        return outputs

    # ------------------------------------------------------------------ internal

    def _run_research_loop(self, prompt: str) -> str:
        """Tool-use loop with rescue path. Returns the final text response."""
        messages: list[dict] = [{"role": "user", "content": prompt}]

        for round_num in range(self.max_rounds):
            resp = self.llm.messages_create(
                system=self.system_prompt,
                messages=messages,
                tools=self.tools,
            )
            tool_calls = [b for b in resp.content
                          if hasattr(b, "type") and b.type == "tool_use"]
            if not tool_calls:
                for b in resp.content:
                    if hasattr(b, "text"):
                        return b.text
                return ""

            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for tc in tool_calls:
                result = self._execute_tool(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tc.id, "content": result,
                })
            messages.append({"role": "user", "content": tool_results})

        # Rescue path — force final output
        logger.warning("CleaningAgent[%s]: hit max_rounds=%d, forcing final output",
                       self.country_code, self.max_rounds)
        messages.append({"role": "user", "content":
            "Research complete. Now return ONLY the table: "
            "| ID | Postal Code | Municipality | Validation Notes |"})
        final = self.llm.messages_create(
            system=self.system_prompt, messages=messages, tools=self.tools,
        )
        for b in final.content:
            if hasattr(b, "text"):
                return b.text
        return ""

    def _execute_tool(self, name: str, args: dict) -> str:
        if name == "web_search":
            query = args.get("query", "")
            result = self.cache.web_search_cached(query, args.get("max_results", 5))
            self._search_log.append(SearchHit(query=query, result=result))
            return result
        return f"Unknown tool: {name}"

    def _format_research_batch(self, records: list[dict]) -> str:
        """Same shape used by the research prompt — see spec §5.1 _format_research_batch."""
        headers = ["ID", "Name", "Address", "City", "Postal Code", "State/Prov", "Country", "Issue"]
        rows = ["| " + " | ".join(headers) + " |",
                "|" + "|".join(["---"] * len(headers)) + "|"]
        for r in records:
            postal = (r.get("postal_code") or "").strip()
            muni = (r.get("municipality") or "").strip()
            postal_chars = re.sub(r"[\s\-]", "", postal)
            issues = []
            if not muni or muni.upper() == "N/A":
                issues.append("municipality missing")
            if not postal_chars or len(postal_chars) < 5:
                issues.append("postal incomplete" if postal_chars else "postal missing")
            rows.append("| " + " | ".join([
                str(r["id"]), r.get("name", ""), r.get("address", ""), r.get("city", ""),
                postal or "N/A", r.get("state_province", ""), r.get("country", ""),
                "; ".join(issues),
            ]) + " |")
        return "\n".join(rows)

    def _parse_research_table(self, response: str) -> dict[int, dict]:
        """Parse 4-column research table response. Same logic as spec §5.1."""
        results: dict[int, dict] = {}
        for line in response.strip().split("\n"):
            if not line.strip().startswith("|") or "---" in line:
                continue
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) < 3:
                continue
            try:
                rid = int(parts[0])
            except ValueError:
                continue
            results[rid] = {
                "postal_code": parts[1] if parts[1] not in ("N/A", "") else None,
                "municipality": parts[2] if parts[2] not in ("N/A", "") else None,
                "validation_notes": parts[3] if len(parts) > 3 else "",
            }
        return results
