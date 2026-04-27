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
    assert stats == {"hits": 1, "misses": 2, "queries_cached": 2}


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
