"""Offline tests for the local HTTP bridge (http.py).

The server runs in-process on an ephemeral port in a thread; every
endpoint is hit with real HTTP requests. No network beyond
localhost, no real keys — a fake key string proves /api/health
never leaks key material.

Run with:  python -m pytest tests/test_http.py -v
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from all_mcp import ask as A  # noqa: E402
from all_mcp import http as H  # noqa: E402
from all_mcp import server as srv  # noqa: E402

FAKE_KEY = "fake-test-key-67890"
GH_ORIGIN = "https://mohammad-abbasi-sh.github.io"
EVIL_ORIGIN = "https://evil.example.com"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "OPENROUTER_API_KEY", "LLM_PROVIDER", "USER_COUNTRY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield


@pytest.fixture()
def bridge():
    httpd = H.make_server(0)  # ephemeral port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _req(base, method, path, body=None, headers=None, raw=None):
    data = raw
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if body is not None or raw is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _json(base, method, path, body=None, headers=None, raw=None):
    status, headers_out, raw_body = _req(
        base, method, path, body, headers, raw)
    return status, headers_out, json.loads(raw_body or b"{}")


# --- health ---------------------------------------------------------------

def test_health(bridge):
    base, httpd = bridge
    assert httpd.server_address[0] == "127.0.0.1"
    status, _, body = _json(base, "GET", "/api/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["servers"] == 60
    assert body["tools"] == 324
    assert body["llm_configured"] is False
    assert body["llm_provider"] is None


def test_health_never_leaks_key(monkeypatch, bridge):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    base, _ = bridge
    status, _, raw = _req(base, "GET", "/api/health")
    assert status == 200
    assert FAKE_KEY.encode() not in raw
    body = json.loads(raw)
    assert body["llm_configured"] is True
    assert body["llm_provider"] == "gemini"


def test_root_info_page(bridge):
    base, _ = bridge
    status, headers, raw = _req(base, "GET", "/")
    assert status == 200
    assert b"all-mcp local bridge" in raw
    assert "text/html" in headers["Content-Type"]


def test_unknown_route_404(bridge):
    base, _ = bridge
    status, _, body = _json(base, "GET", "/api/nope")
    assert status == 404
    assert "error" in body


# --- search / call ----------------------------------------------------------

def test_search_endpoint(bridge):
    base, _ = bridge
    status, _, body = _json(base, "POST", "/api/search",
                            {"query": "dollar exchange rate today"})
    assert status == 200
    assert body["count"] >= 1
    assert body["results"][0]["tool"] == "tcmb_get_rates"


def test_search_requires_query(bridge):
    base, _ = bridge
    status, _, body = _json(base, "POST", "/api/search", {"query": " "})
    assert status == 400


def test_call_unknown_server_is_error_envelope(bridge):
    base, _ = bridge
    status, _, body = _json(base, "POST", "/api/call",
                            {"server": "nope-mcp", "tool": "nope"})
    assert status == 200  # MCP-style envelope, not an HTTP error
    assert "Unknown server" in body["error"]


def test_call_requires_server_and_tool(bridge):
    base, _ = bridge
    status, _, body = _json(base, "POST", "/api/call",
                            {"server": "tcmb-mcp"})
    assert status == 400


def test_invalid_json_is_400(bridge):
    base, _ = bridge
    status, _, body = _json(base, "POST", "/api/search",
                            raw=b"{not json")
    assert status == 400
    assert "invalid JSON" in body["error"]


# --- ask --------------------------------------------------------------------

def test_ask_fallback_steps_and_sources(monkeypatch, bridge):
    """Ask without a key: top result auto-executed; steps + sources
    present and well-formed (the chat UI's routing trace)."""
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1],
        "result": {"USD": "43.1",
                   "source_url": "https://www.tcmb.gov.tr/"},
        "is_error": False})
    base, _ = bridge
    status, _, body = _json(base, "POST", "/api/ask",
                            {"question": "dollar exchange rate today"})
    assert status == 200
    assert body["mode"] == "search-fallback"
    assert body["executed"] is True
    steps = body["steps"]
    assert len(steps) == 2
    for s in steps:
        assert set(s) == {"server", "tool", "status", "ms"}
        assert s["status"] in ("ok", "failed", "skipped")
        assert isinstance(s["ms"], int) and s["ms"] >= 0
    assert steps[0]["tool"] == "route"
    assert steps[1]["server"] == "tcmb-mcp"
    assert steps[1]["status"] == "ok"
    urls = {src["url"] for src in body["sources"]}
    assert "https://www.tcmb.gov.tr/" in urls
    assert any("tcmb-mcp on GitHub" == src["title"]
               for src in body["sources"])
    assert "tcmb-mcp/tcmb_get_rates ok" in body["trace"]
    assert body["alternatives"]


def test_ask_with_mocked_llm(monkeypatch, bridge):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(A, "route_question",
                        lambda q, c, p, **k: {
                            "server": "flixtrain-mcp",
                            "tool": "flixtrain_search_trains",
                            "arguments": {"from_city": "Berlin",
                                          "to_city": "München",
                                          "date": "19.11.2026"},
                            "reason": "German train"})
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1],
        "result": {"trains": []}, "is_error": False})
    base, _ = bridge
    status, _, body = _json(
        base, "POST", "/api/ask",
        {"question": "قیمت بلیط قطار از برلین به مونیخ چقدره؟"})
    assert status == 200
    assert body["mode"] == "llm"
    assert body["provider"] == "gemini"
    assert body["executed"] is True
    assert body["routing"]["tool"] == "flixtrain_search_trains"
    assert body["steps"][1]["server"] == "flixtrain-mcp"
    assert FAKE_KEY not in json.dumps(body)


def test_ask_location_hint_beats_saved(monkeypatch, bridge):
    from all_mcp import location as loc_mod

    monkeypatch.setattr(
        loc_mod, "resolve_location",
        lambda: {"country": "TR", "city": None, "source": "file"})
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1], "result": {}, "is_error": False})
    base, _ = bridge
    status, _, body = _json(
        base, "POST", "/api/ask",
        {"question": "قیمت بلیط اتوبوس",
         "location": {"country": "DE", "city": "Berlin"}})
    assert status == 200
    assert body["alternatives"][0]["country"] == "DE"
    assert body["location"] == {
        "country": "DE", "city": "Berlin", "source": "hint"}


def test_ask_invalid_location_is_400(bridge):
    base, _ = bridge
    status, _, body = _json(
        base, "POST", "/api/ask",
        {"question": "hello", "location": {"country": "XX"}})
    assert status == 400
    assert "Invalid location country" in body["error"]


def test_ask_requires_question(bridge):
    base, _ = bridge
    status, _, body = _json(base, "POST", "/api/ask", {"question": ""})
    assert status == 400


# --- CORS -------------------------------------------------------------------

def test_cors_github_pages_origin(bridge):
    base, _ = bridge
    status, headers, _ = _req(base, "GET", "/api/health",
                              headers={"Origin": GH_ORIGIN})
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == GH_ORIGIN


def test_cors_localhost_origin(bridge):
    base, _ = bridge
    status, headers, _ = _req(base, "GET", "/api/health",
                              headers={"Origin": "http://localhost:3000"})
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == \
        "http://localhost:3000"


def test_cors_evil_origin_gets_nothing(bridge):
    base, _ = bridge
    status, headers, _ = _req(base, "GET", "/api/health",
                              headers={"Origin": EVIL_ORIGIN})
    assert status == 200
    assert "Access-Control-Allow-Origin" not in headers


def test_options_preflight(bridge):
    base, _ = bridge
    status, headers, _ = _req(
        base, "OPTIONS", "/api/ask",
        headers={"Origin": GH_ORIGIN,
                 "Access-Control-Request-Method": "POST"})
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") == GH_ORIGIN
    assert "POST" in headers.get("Access-Control-Allow-Methods", "")
