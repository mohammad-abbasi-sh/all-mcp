"""Offline tests for the server-level do_* implementations
(no MCP transport, no child processes).

Run with:  python -m pytest tests/test_server.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from all_mcp import server as srv  # noqa: E402


def test_search_envelope():
    out = srv.do_search("قیمت بلیط قطار از برلین به مونیخ چقدره؟")
    assert out["count"] >= 1
    assert out["results"][0]["tool"] == "flixtrain_search_trains"
    assert "hint" in out


def test_call_unknown_server():
    out = srv.do_call("nope-mcp", "whatever")
    assert "error" in out and "Unknown server" in out["error"]


def test_call_unknown_tool():
    out = srv.do_call("tcmb-mcp", "nope_tool")
    assert "error" in out and "no tool" in out["error"]
    assert "tcmb_get_rates" in out["error"]


def test_call_pending_pypi_real_tool_fails_fast():
    out = srv.do_call("osym-mcp", "osym_latest_announcements")
    assert out.get("reason") == "not_on_pypi"
    assert "not published on PyPI" in out["error"]


def test_call_bad_timeout():
    out = srv.do_call("tcmb-mcp", "tcmb_get_rates", timeout=1)
    assert "error" in out and "timeout" in out["error"]


def test_list_servers_filters():
    all_servers = srv.do_list_servers()
    assert all_servers["count"] == 60
    de = srv.do_list_servers(country="DE")
    assert de["count"] == 27
    assert all(s["country"] == "DE" for s in de["servers"])
    tr = srv.do_list_servers(catalog="turkey")
    assert tr["count"] == 32
    transport = srv.do_list_servers(category="transport")
    assert {s["id"] for s in transport["servers"]} == {
        "flixtrain-mcp", "autobahn-mcp"}


def test_health_counts_and_pending():
    health = srv.do_health()
    assert health["status"] == "ok"
    assert health["servers"] == 60
    assert health["tools"] == 324
    assert set(health["not_on_pypi"]) == {
        "paribu-mcp", "diyanet-mcp", "osym-mcp",
        "heise-mcp", "zeit-mcp", "welt-mcp"}
    assert health["on_pypi"] == 54
    assert health["by_catalog"]["turkey"] == {"servers": 32, "tools": 186}
