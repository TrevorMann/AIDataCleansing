"""Domain-agnostic web search enricher skill."""

from importlib import import_module
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from skills.base import BaseSkill


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


class WebSearchEnricher(BaseSkill):
    """Resolve missing fields via Tavily web search. Domain-agnostic core."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.max_queries = self.config.get("max_queries", 3)
        self.confidence_trigger = self.config.get("trigger_below", 0.70)
        self.cache = self.config.get("web_cache")
        self.conn = self.config.get("pg_conn")

    def run(self, record: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        # Hard gates
        if record.get("_triage_route") in ("done", "unsalvageable"):
            return record
        if record.get("_triage_data_confidence", 1.0) >= self.confidence_trigger:
            return record

        gaps = self._identify_gaps(record)
        if not gaps:
            return record

        # Check batch budget if provided in tools
        budget_obj = (tools or {}).get("batch_budget")

        evidence = []
        decisions = []
        budget = self.max_queries

        for gap in gaps:
            if budget <= 0:
                break

            queries = self._get_queries(gap)
            if not queries:
                continue

            parser = self._load_parser(gap)

            for q_template in queries:
                if budget <= 0:
                    break

                # Check + take from batch budget
                if budget_obj and not budget_obj.take():
                    decisions.append(self.log_decision(
                        f"Batch budget exhausted — skipping {gap}",
                        "BatchBudget.take() returned False",
                        confidence=0.0,
                    ))
                    break

                try:
                    query = q_template.format(**record)
                except KeyError:
                    continue  # missing field — try next template

                budget -= 1
                search_text = self._search(query)

                if search_text is None:
                    continue

                parsed = parser.parse(search_text, record) if parser else None
                if parsed:
                    record.update(parsed["fields"])
                    evidence.append({
                        "query": query,
                        "gap": gap,
                        "url": parsed.get("source_url"),
                        "snippet": parsed.get("snippet"),
                    })
                    self._record_outcome(gap, q_template, success=True, url=parsed.get("source_url"))
                    decisions.append(self.log_decision(
                        f"Resolved {gap} via web search",
                        f"Query: '{query}' → {parsed['fields']}",
                        confidence=parsed.get("confidence", 0.75),
                    ))
                    break
            else:
                self._record_outcome(gap, queries[0] if queries else "", success=False)
                decisions.append(self.log_decision(
                    f"Web search found no parsable result for {gap}",
                    f"Tried {len(queries)} queries",
                    confidence=0.0,
                ))

        record["_web_search_evidence"] = evidence
        if decisions:
            record.setdefault("_decisions", []).extend(decisions)
        return record

    def _identify_gaps(self, record: dict) -> List[str]:
        gaps = []
        if record.get("_unknown_fsa"):
            gaps.append("postal_unresolved")
        if record.get("_municipality_confidence", 1.0) < 0.70:
            gaps.append("municipality_ambiguous")
        if not record.get("country"):
            gaps.append("unknown_country")
        gaps.extend(record.get("_gap_hints", []))
        return list(dict.fromkeys(gaps))  # dedupe, preserve order

    def _get_queries(self, gap_type: str) -> List[str]:
        """Get query templates: DB first, then fallback from query packs YAML."""
        if self.conn:
            try:
                from db.pg_query_memory import top_queries_for
                queries = top_queries_for(self.conn, self.domain, gap_type, k=2)
                if queries:
                    return queries
                # Fallback to _common
                queries = top_queries_for(self.conn, "_common", gap_type, k=2)
                if queries:
                    return queries
            except Exception:
                pass
        return []

    def _search(self, query: str) -> Optional[str]:
        """Run search via WebSearchCache (Tavily). Returns text or None."""
        if self.cache is None:
            return None
        try:
            result = self.cache.get_or_search(query)
            if isinstance(result, dict):
                # Extract text from Tavily result structure
                snippets = [r.get("content", "") for r in result.get("results", [])]
                urls = [r.get("url", "") for r in result.get("results", [])]
                combined = " ".join(snippets)
                if urls:
                    combined += " " + " URL: ".join(urls)
                return combined or None
            if isinstance(result, str):
                return result or None
        except Exception:
            pass
        return None

    def _load_parser(self, gap_type: str):
        """Load parser module: domain-specific, fallback to _common."""
        domain = getattr(self, "domain", "_common")
        for d in (domain, "_common"):
            try:
                return import_module(
                    f"skills._common.web_search_enricher.parsers.{d}.{gap_type}"
                )
            except ModuleNotFoundError:
                continue
        return None

    def _record_outcome(self, gap_type: str, template: str, success: bool, url: str = None):
        if not self.conn or not template:
            return
        try:
            from db.pg_query_memory import record_query_outcome, update_source_score
            record_query_outcome(self.conn, self.domain, gap_type, template, success)
            if url and success:
                update_source_score(self.conn, self.domain, _host(url), success=True)
        except Exception:
            pass
