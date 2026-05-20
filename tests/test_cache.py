"""Tests for cleaning/cache.py — WebSearchCache.

Uses mocks for the Tavily HTTP call and PG connection so no network/DB needed.
Positive + negative cases for every public method.
"""
from unittest.mock import MagicMock, patch
import pytest
from cleaning.cache import WebSearchCache, _normalize_query, _is_error_result


# ── helpers ────────────────────────────────────────────────────────────────────

class TestNormalizeQuery:
    def test_lowercases(self):
        assert _normalize_query("TORONTO") == "toronto"

    def test_collapses_whitespace(self):
        assert _normalize_query("  two   spaces  ") == "two spaces"

    def test_strips_trailing_punctuation(self):
        assert _normalize_query("toronto?") == "toronto"
        assert _normalize_query("toronto.") == "toronto"

    def test_idempotent(self):
        q = "postal code M5V"
        assert _normalize_query(_normalize_query(q)) == _normalize_query(q)


class TestIsErrorResult:
    def test_tavily_error_prefix(self):
        assert _is_error_result("Web search failed: timeout")

    def test_error_prefix(self):
        assert _is_error_result("Error: API key not set")

    def test_valid_result_is_not_error(self):
        assert not _is_error_result("Toronto is in Ontario.")

    def test_empty_string_is_not_error(self):
        assert not _is_error_result("")


# ── cache hit / miss ───────────────────────────────────────────────────────────

class TestInMemoryCache:
    def test_miss_calls_tavily(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="result text") as mock_call:
            result = cache.web_search_cached("toronto")
        mock_call.assert_called_once()
        assert result == "result text"

    def test_hit_does_not_call_tavily(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="result text"):
            cache.web_search_cached("toronto")
        with patch("cleaning.cache._tavily_call") as mock_call:
            cache.web_search_cached("toronto")
        mock_call.assert_not_called()

    def test_get_or_search_aliases_web_search_cached(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="alias result"):
            r1 = cache.web_search_cached("query")
        with patch("cleaning.cache._tavily_call", return_value="alias result"):
            r2 = cache.get_or_search("query")
        assert r1 == r2

    def test_get_or_search_uses_cache_on_second_call(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="first") as m:
            cache.get_or_search("q")
        with patch("cleaning.cache._tavily_call") as m2:
            cache.get_or_search("q")
        m2.assert_not_called()

    def test_case_insensitive_normalization(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="result"):
            cache.web_search_cached("Toronto Postal")
        with patch("cleaning.cache._tavily_call") as m:
            cache.web_search_cached("toronto postal")
        m.assert_not_called()

    def test_error_result_not_cached(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="Web search failed: timeout"):
            cache.web_search_cached("bad query")
        with patch("cleaning.cache._tavily_call", return_value="good result") as m:
            result = cache.web_search_cached("bad query")
        m.assert_called_once()
        assert result == "good result"


# ── stats ──────────────────────────────────────────────────────────────────────

class TestStats:
    def test_initial_stats_are_zero(self):
        cache = WebSearchCache()
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["queries_cached"] == 0

    def test_miss_increments_misses(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="r"):
            cache.web_search_cached("q1")
        assert cache.stats()["misses"] == 1
        assert cache.stats()["hits"] == 0

    def test_hit_increments_hits(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="r"):
            cache.web_search_cached("q1")
            cache.web_search_cached("q1")
        s = cache.stats()
        assert s["misses"] == 1
        assert s["hits"] == 1

    def test_queries_cached_count(self):
        cache = WebSearchCache()
        with patch("cleaning.cache._tavily_call", return_value="r"):
            cache.web_search_cached("q1")
            cache.web_search_cached("q2")
        assert cache.stats()["queries_cached"] == 2


# ── PG cache layer ─────────────────────────────────────────────────────────────

class TestPgCacheLayer:
    def test_pg_hit_skips_tavily(self):
        mock_pg = MagicMock()
        with patch("cleaning.cache.search_cache_lookup", return_value="pg cached result"):
            cache = WebSearchCache(pg_conn=mock_pg)
            with patch("cleaning.cache._tavily_call") as m:
                result = cache.web_search_cached("toronto")
            m.assert_not_called()
        assert result == "pg cached result"

    def test_pg_miss_falls_through_to_tavily(self):
        mock_pg = MagicMock()
        with patch("cleaning.cache.search_cache_lookup", return_value=None):
            cache = WebSearchCache(pg_conn=mock_pg)
            with patch("cleaning.cache._tavily_call", return_value="live result"):
                with patch("cleaning.cache.search_cache_store") as store_mock:
                    result = cache.web_search_cached("toronto")
        assert result == "live result"
        store_mock.assert_called_once()

    def test_pg_store_not_called_for_error_result(self):
        mock_pg = MagicMock()
        with patch("cleaning.cache.search_cache_lookup", return_value=None):
            cache = WebSearchCache(pg_conn=mock_pg)
            with patch("cleaning.cache._tavily_call", return_value="Web search failed: err"):
                with patch("cleaning.cache.search_cache_store") as store_mock:
                    cache.web_search_cached("bad")
        store_mock.assert_not_called()

    def test_pg_hit_increments_pg_hits_stat(self):
        mock_pg = MagicMock()
        with patch("cleaning.cache.search_cache_lookup", return_value="pg hit"):
            cache = WebSearchCache(pg_conn=mock_pg)
            cache.web_search_cached("q")
        assert cache.stats()["pg_hits"] == 1
