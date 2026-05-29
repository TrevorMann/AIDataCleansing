"""Tests for cleaning.cache.WebSearchCache."""
from unittest.mock import MagicMock
import threading
import pytest


def test_normalization_collapses_whitespace_and_case():
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    c.put("M6H Toronto Postal", "result1")
    assert c.get("m6h  toronto  postal ") == "result1"


def test_get_returns_none_on_miss():
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    assert c.get("never seen") is None


def test_stats_tracks_hits_misses_and_queries(mock_tavily):
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    mock_tavily.return_value = "X"
    c.web_search_cached("foo")  # miss
    c.web_search_cached("foo")  # hit
    c.web_search_cached("bar")  # miss
    stats = c.stats()
    assert stats == {"hits": 1, "misses": 2,"pg_hits": 0, "queries_cached": 2}


def test_errors_are_not_cached(mock_tavily):
    from cleaning.cache import WebSearchCache
    c = WebSearchCache()
    mock_tavily.return_value = "Web search failed: boom. Query: q"
    c.web_search_cached("q")
    assert c.get("q") is None  # error response not cached
    mock_tavily.return_value = "real result"
    assert c.web_search_cached("q") == "real result"


def test_thread_safety_no_lost_writes(mock_tavily):
    """50 threads all put-and-get; verify no exception and final state is consistent."""
    from cleaning.cache import WebSearchCache
    mock_tavily.side_effect = lambda q, max_results=5: f"r:{q}"
    c = WebSearchCache()
    errors = []
    def worker(i):
        try:
            for j in range(20):
                c.web_search_cached(f"q{i}_{j}")
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == []
    assert c.stats()["queries_cached"] == 50 * 20


def test_stats_consistent_under_contention(mock_tavily):
    """Concurrent queries to the same key must not produce negative or impossible stats."""
    from cleaning.cache import WebSearchCache
    mock_tavily.side_effect = lambda q, max_results=5: f"result:{q}"
    c = WebSearchCache()
    results = []
    def worker():
        results.append(c.web_search_cached("same_query"))
    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    stats = c.stats()
    # All threads return the correct result
    assert all(r == "result:same_query" for r in results)
    # Total hits + misses == total calls
    assert stats["hits"] + stats["misses"] == 20
    # At most one query cached
    assert stats["queries_cached"] == 1
