"""Offline tests for location-first routing.

Priority under test: explicit place names in the query BEAT the
saved location; the saved location beats no default; with no cue
and no location, both countries are returned ranked.

Run with:  python -m pytest tests/test_location.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from all_mcp import location as loc  # noqa: E402
from all_mcp.index import SearchIndex, load_index  # noqa: E402

SI = SearchIndex(load_index())
BARE_BUS = "قیمت بلیط اتوبوس"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("USER_COUNTRY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield


def test_bare_bus_with_de_location_ranks_de_first():
    results = SI.search(BARE_BUS, location_country="DE")
    assert results[0]["country"] == "DE"


def test_bare_bus_with_tr_location_ranks_tr_first():
    results = SI.search(BARE_BUS, location_country="TR")
    assert results[0]["country"] == "TR"


def test_explicit_place_beats_saved_location():
    """Munich->Hamburg bus with TR saved: DE still first."""
    results = SI.search("اتوبوس مونیخ به هامبورگ",
                        location_country="TR")
    assert results[0]["country"] == "DE"


def test_saved_location_does_not_hijack_foreign_topic():
    """A DE user asking about the Turkish YKS exam still gets osym
    (no German tool has a real topical match)."""
    results = SI.search("آزمون YKS", location_country="DE")
    assert results, "no results"
    assert results[0]["server"] == "osym-mcp"


def test_env_beats_file(monkeypatch, tmp_path):
    loc.save_location("TR", "Ankara")
    monkeypatch.setenv("USER_COUNTRY", "DE")
    resolved = loc.resolve_location()
    assert resolved == {"country": "DE", "city": None, "source": "env"}


def test_file_used_when_no_env():
    loc.save_location("DE", "Berlin")
    assert loc.resolve_location() == {
        "country": "DE", "city": "Berlin", "source": "file"}


def test_no_location_when_nothing_set():
    assert loc.resolve_location() is None


def test_save_writes_local_file_only(tmp_path):
    out = loc.save_location("TR")
    path = Path(tmp_path) / "all-mcp" / "location.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["country"] == "TR"
    assert out["saved"]["country"] == "TR"
    assert out["effective"]["country"] == "TR"


def test_save_rejects_bad_country():
    with pytest.raises(ValueError):
        loc.save_location("US")
    with pytest.raises(ValueError):
        loc.save_location("")


def test_save_notes_env_override(monkeypatch):
    monkeypatch.setenv("USER_COUNTRY", "DE")
    out = loc.save_location("TR")
    assert out["effective"]["country"] == "DE"
    assert "USER_COUNTRY" in out["note"]


def test_health_reports_location(monkeypatch):
    from all_mcp import server as srv  # noqa: E402

    monkeypatch.setenv("USER_COUNTRY", "DE")
    health = srv.do_health()
    assert health["location"]["country"] == "DE"
    assert health["location"]["source"] == "env"
    assert health["servers"] == 60
    assert health["tools"] == 324
