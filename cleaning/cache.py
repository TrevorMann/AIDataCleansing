"""Cached wrapper around the Tavily search API.

Shared across all CleaningAgents and the EscalationAgent within one workflow run.
Thread-safe from day one so the future A migration (parallel agents) does not
require touching this file.
"""
import json
import os
import re
import threading
import urllib.request

from db.pg_vector import search_cache_lookup, search_cache_store


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize_query(query: str) -> str:
    """lowercase, collapse whitespace, strip trailing punctuation."""
    q = _NORMALIZE_RE.sub(" ", query.lower().strip())
    return q.rstrip(".,;:!?")


def _is_error_result(result: str) -> bool:
    """Tavily error strings start with 'Web search failed' or 'Error:'."""
    return result.startswith("Web search failed") or result.startswith("Error:")


def _tavily_call(query: str, max_results: int = 5) -> str:
    """Hit the Tavily Search API. Returns formatted result string or error string."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not set in environment."

    try:
        payload = json.dumps(
            {
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": True,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        parts = []
        if data.get("answer"):
            parts.append(f"Summary: {data['answer']}\n")
        for i, r in enumerate(data.get("results", [])[:max_results], 1):
            parts.append(
                f"{i}. {r.get('title', 'No title')}\n"
                f"   {r.get('content', '')[:300]}\n"
                f"   URL: {r.get('url', '')}"
            )
        return "\n".join(parts) if parts else f"No results found for: {query}"
    except Exception as e:
        return f"Web search failed: {e}. Query: {query}"


class WebSearchCache:
    def __init__(self, pg_conn=None) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._pg_hits = 0
        self._pg_conn = pg_conn

    def get(self, query: str) -> str | None:
        key = _normalize_query(query)
        with self._lock:
            return self._store.get(key)

    def put(self, query: str, result: str) -> None:
        if _is_error_result(result):
            return
        key = _normalize_query(query)
        with self._lock:
            self._store[key] = result

    def web_search_cached(self, query: str, max_results: int = 5) -> str:
        key = _normalize_query(query)
        with self._lock:
            if key in self._store:
                self._hits += 1
                return self._store[key]
            self._misses += 1

        if self._pg_conn is not None:
            try:
                cached = search_cache_lookup(self._pg_conn, key)
                if cached is not None:
                    with self._lock:
                        self._store[key] = cached
                        self._pg_hits += 1
                    return cached
            except Exception:
                pass

        result = _tavily_call(query, max_results)
        self.put(query, result)
        if self._pg_conn is not None and not _is_error_result(result):
            try:
                search_cache_store(self._pg_conn, key, result)
            except Exception:
                pass
        return result

    def get_or_search(self, query: str, max_results: int = 5) -> str:
        return self.web_search_cached(query, max_results)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "pg_hits": self._pg_hits,
                "queries_cached": len(self._store),
            }
