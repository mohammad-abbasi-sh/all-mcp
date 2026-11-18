"""Offline ranking tests for search (real generated index, no network).

Includes Mohammad's acceptance tests:
1. Persian train-ticket question -> flixtrain tools on top.
2. Munich->Hamburg bus question -> GERMANY transport above TR.
3. Bare bus question with no place names -> BOTH countries ranked.

Run with:  python -m pytest tests/test_search.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from all_mcp.index import SearchIndex, load_index  # noqa: E402

SI = SearchIndex(load_index())


def _first_country(results):
    return results[0]["country"] if results else None


def _best_rank(results, servers):
    ranks = [i for i, r in enumerate(results) if r["server"] in servers]
    return min(ranks) if ranks else None


# --- Mohammad's acceptance tests -------------------------------------

def test_train_acceptance_persian():
    """«قیمت بلیط قطار از برلین به مونیخ چقدره؟» -> flixtrain #1."""
    results = SI.search("قیمت بلیط قطار از برلین به مونیخ چقدره؟")
    assert results, "no results"
    assert results[0]["server"] == "flixtrain-mcp"
    assert results[0]["tool"] == "flixtrain_search_trains"
    assert results[0]["country"] == "DE"


def test_bus_munich_hamburg_routes_to_germany():
    """«قیمت اتوبوس مونیخ به هامبورگ» -> DE first, and DE transport
    (flixtrain/autobahn) above TR transport (obilet/biletall)."""
    results = SI.search("قیمت اتوبوس مونیخ به هامبورگ")
    assert _first_country(results) == "DE"
    de = _best_rank(results, {"flixtrain-mcp", "autobahn-mcp"})
    tr = _best_rank(results, {"obilet-mcp", "biletall-mcp"})
    assert de is not None, "no DE transport in results"
    assert tr is None or de < tr, (
        f"DE transport rank {de} not above TR transport rank {tr}")


def test_bare_bus_query_returns_both_countries():
    """«قیمت بلیط اتوبوس» with no place names: both DE and TR options
    ranked, not silently one country — the LLM then asks the user."""
    results = SI.search("قیمت بلیط اتوبوس")
    countries = {r["country"] for r in results}
    assert "DE" in countries and "TR" in countries


# --- multilingual routing --------------------------------------------

def test_english_exchange_rate():
    results = SI.search("dollar exchange rate today")
    assert results[0]["server"] == "tcmb-mcp"


def test_turkish_exchange_rate():
    results = SI.search("döviz kuru bugün")
    assert results[0]["server"] == "tcmb-mcp"


def test_german_football():
    results = SI.search("Fußball Ergebnisse Bundesliga")
    assert results[0]["server"] == "bundesliga-mcp"


def test_persian_weather_berlin():
    results = SI.search("هوای برلین چطوره")
    assert results[0]["server"] == "dwd-mcp"
    assert results[0]["country"] == "DE"


def test_persian_gorgan_discount():
    results = SI.search("تخفیف گرگان")
    assert results[0]["server"] == "bonbama-mcp"
    assert results[0]["country"] == "IR"


def test_istanbul_football_prefers_turkish():
    results = SI.search("فوتبال استانبول")
    assert _first_country(results) == "TR"


# --- result shape + guards --------------------------------------------

def test_results_carry_country_and_description():
    for r in SI.search("train ticket berlin munich"):
        assert r["country"] in ("DE", "TR", "IR")
        assert r["description"], r
        assert r["server"] and r["tool"]


def test_max_two_results_per_server():
    from collections import Counter
    for q in ("قیمت بلیط اتوبوس", "news", "قیمت"):
        counts = Counter(r["server"] for r in SI.search(q, limit=20))
        assert max(counts.values()) <= 2, (q, counts)


def test_empty_query_rejected():
    with pytest.raises(ValueError):
        SI.search("   ")


def test_limit_bounds():
    with pytest.raises(ValueError):
        SI.search("train", limit=0)
    with pytest.raises(ValueError):
        SI.search("train", limit=21)
    assert len(SI.search("train", limit=3)) <= 3


def test_stopwords_do_not_match():
    """Function words alone must not rank random servers: a query of
    only stopwords returns nothing."""
    assert SI.search("به از در و که") == []
    assert SI.search("what is the") == []
