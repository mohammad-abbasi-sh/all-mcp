"""Offline tests for the provider-agnostic `ask` routing.

All provider HTTP calls are mocked — no network, no real keys.
A fake key string is used to prove keys never leak into outputs.

Run with:  python -m pytest tests/test_ask.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from all_mcp import ask as A  # noqa: E402
from all_mcp import server as srv  # noqa: E402
from all_mcp.index import load_index  # noqa: E402

DATA = load_index()
CATALOG = A.build_catalog_text(DATA)
FAKE_KEY = "fake-test-key-12345"
KEY_VARS = ["GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY"]


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch, tmp_path):
    for var in KEY_VARS + ["LLM_PROVIDER", "USER_COUNTRY"]:
        monkeypatch.delenv(var, raising=False)
    # No saved location file: ranking must come only from the test's
    # own hint or an explicit monkeypatch of resolve_location.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield


def _assert_steps_well_formed(out):
    steps = out["steps"]
    assert isinstance(steps, list) and steps
    for s in steps:
        assert set(s) == {"server", "tool", "status", "ms"}
        assert isinstance(s["server"], str) and s["server"]
        assert isinstance(s["tool"], str) and s["tool"]
        assert s["status"] in ("ok", "failed", "skipped")
        assert isinstance(s["ms"], int) and s["ms"] >= 0
    sources = out["sources"]
    assert isinstance(sources, list)
    for src in sources:
        assert set(src) == {"title", "url"}
        assert src["url"].startswith("https://")
    # The compact trace has one line per step.
    for s in steps:
        assert f"{s['server']}/{s['tool']} {s['status']}" in out["trace"]


# --- catalog ------------------------------------------------------------

def test_catalog_covers_all_tools():
    import re
    assert "flixtrain_search_trains" in CATALOG
    assert "tcmb_get_rates" in CATALOG
    assert "bb_search_discounts" in CATALOG
    tool_lines = [ln for ln in CATALOG.splitlines()
                  if re.match(r"^[a-z0-9-]+ \[(DE|TR|IR)\] ", ln)]
    assert len(tool_lines) == 324


def test_catalog_marks_unavailable_servers():
    osym_lines = [ln for ln in CATALOG.splitlines()
                  if ln.startswith("osym-mcp")]
    assert osym_lines and all("[UNAVAILABLE]" in ln for ln in osym_lines)
    tcmb_lines = [ln for ln in CATALOG.splitlines()
                  if ln.startswith("tcmb-mcp")]
    assert all("[UNAVAILABLE]" not in ln for ln in tcmb_lines)


def test_catalog_includes_country_tags():
    assert "flixtrain-mcp [DE]" in CATALOG
    assert "tcmb-mcp [TR]" in CATALOG
    assert "bonbama-mcp [IR]" in CATALOG


# --- provider selection ---------------------------------------------------

def test_no_provider_when_no_keys():
    assert A.select_provider() is None


def test_selection_order_gemini_first(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    assert A.select_provider() == "gemini"


def test_selection_order_openai_over_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    assert A.select_provider() == "openai"


def test_openrouter_when_only_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    assert A.select_provider() == "openrouter"


def test_llm_provider_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert A.select_provider() == "openai"


def test_llm_provider_ollama_needs_no_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert A.select_provider() == "ollama"


def test_ollama_never_auto_selected():
    # No keys at all: ollama must NOT be auto-picked, or the no-key
    # search fallback would break.
    assert A.select_provider() is None


def test_override_with_missing_key_is_a_clean_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(A.AskError) as e:
        A.select_provider()
    assert "OPENAI_API_KEY" in str(e.value)


def test_unknown_provider_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    with pytest.raises(A.AskError):
        A.select_provider()


# --- per-provider response parsing (mocked HTTP) --------------------------

def _mock_post(monkeypatch, payload):
    def fake(url, headers, body, timeout):
        return payload
    monkeypatch.setattr(A, "_post_json", fake)


def test_gemini_routing_parsed(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    _mock_post(monkeypatch, {"candidates": [{"content": {"parts": [
        {"text": '{"server": "flixtrain-mcp", "tool": '
                 '"flixtrain_search_trains", "arguments": '
                 '{"from_city": "Berlin", "to_city": "München", '
                 '"date": "19.11.2026"}, "reason": "German train"}'}]}}]})
    out = A.route_question("train berlin munich", CATALOG, "gemini")
    assert out["server"] == "flixtrain-mcp"
    assert out["tool"] == "flixtrain_search_trains"
    assert out["arguments"]["to_city"] == "München"


def test_openai_routing_parsed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    _mock_post(monkeypatch, {"choices": [{"message": {"content":
        '{"server": "tcmb-mcp", "tool": "tcmb_get_rates", '
        '"arguments": {}, "reason": "FX rates"}'}}]})
    out = A.route_question("dollar rate", CATALOG, "openai")
    assert out == {"server": "tcmb-mcp", "tool": "tcmb_get_rates",
                   "arguments": {}, "reason": "FX rates"}


def test_openrouter_routing_parsed(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    _mock_post(monkeypatch, {"choices": [{"message": {"content":
        '{"server": "dwd-mcp", "tool": "dwd_current_weather", '
        '"arguments": {"place": "Berlin"}, "reason": "weather"}'}}]})
    out = A.route_question("weather berlin", CATALOG, "openrouter")
    assert out["server"] == "dwd-mcp"


def test_anthropic_routing_parsed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    _mock_post(monkeypatch, {"content": [{"type": "text", "text":
        'Here is the route: {"server": "bonbama-mcp", "tool": '
        '"bb_search_discounts", "arguments": {"query": "کفش"}, '
        '"reason": "discounts"}'}]})
    out = A.route_question("تخفیف کفش گرگان", CATALOG, "anthropic")
    assert out["server"] == "bonbama-mcp"
    assert out["arguments"] == {"query": "کفش"}


def test_ollama_routing_parsed(monkeypatch):
    _mock_post(monkeypatch, {"message": {"content":
        '{"server": "bundesliga-mcp", "tool": '
        '"bundesliga_get_live_scores", "arguments": {}, '
        '"reason": "live scores"}'}})
    out = A.route_question("bundesliga scores", CATALOG, "ollama")
    assert out["tool"] == "bundesliga_get_live_scores"


def test_bad_json_is_a_clean_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    _mock_post(monkeypatch, {"candidates": [{"content": {"parts": [
        {"text": "sorry, I cannot help with that"}]}}]})
    with pytest.raises(A.AskError):
        A.route_question("hello", CATALOG, "gemini")


def test_http_error_never_leaks_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)

    def boom(url, headers, body, timeout):
        raise A.AskError("Routing provider returned HTTP 401: bad key")
    monkeypatch.setattr(A, "_post_json", boom)
    with pytest.raises(A.AskError) as e:
        A.route_question("hello", CATALOG, "gemini")
    assert FAKE_KEY not in str(e.value)


# --- do_ask dispatch ------------------------------------------------------

def test_ask_llm_dispatch(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(A, "route_question", lambda q, c, p, **k: {
        "server": "tcmb-mcp", "tool": "tcmb_get_rates",
        "arguments": {}, "reason": "FX rates for today"})
    calls = {}

    def fake_call(server, tool, arguments=None, timeout=90):
        calls.update(server=server, tool=tool, arguments=arguments)
        return {"server": server, "tool": tool,
                "result": {"USD": "43.1"}, "is_error": False}
    monkeypatch.setattr(srv, "do_call", fake_call)
    out = srv.do_ask("dollar exchange rate today")
    assert out["mode"] == "llm"
    assert out["provider"] == "gemini"
    assert out["executed"] is True
    assert out["routing"]["reason"] == "FX rates for today"
    assert out["result"] == {"USD": "43.1"}
    assert calls == {"server": "tcmb-mcp", "tool": "tcmb_get_rates",
                     "arguments": {}}
    assert out["alternatives"], "alternatives missing"
    _assert_steps_well_formed(out)
    assert out["steps"][0]["tool"] == "route"
    assert out["steps"][1] == {
        "server": "tcmb-mcp", "tool": "tcmb_get_rates",
        "status": "ok", "ms": out["steps"][1]["ms"]}
    assert out["sources"][0]["title"] == "tcmb-mcp on GitHub"


def test_ask_unknown_server_is_clean_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(A, "route_question", lambda q, c, p, **k: {
        "server": "nope-mcp", "tool": "nope", "arguments": {},
        "reason": "x"})
    called = []
    monkeypatch.setattr(srv, "do_call",
                        lambda *a, **k: called.append(True))
    out = srv.do_ask("hello")
    assert "unknown server" in out["error"].lower()
    assert called == []


def test_ask_unknown_tool_is_clean_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(A, "route_question", lambda q, c, p, **k: {
        "server": "tcmb-mcp", "tool": "nope_tool", "arguments": {},
        "reason": "x"})
    out = srv.do_ask("hello")
    assert "unknown tool" in out["error"].lower()


def test_ask_no_tool_fits(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(A, "route_question", lambda q, c, p, **k: {
        "server": None, "tool": None, "arguments": {},
        "reason": "nothing covers this"})
    out = srv.do_ask("how do I bake bread?")
    assert out["executed"] is False
    assert "No tool" in out["error"]


def test_ask_fallback_executes_no_arg_top_result(monkeypatch):
    calls = {}

    def fake_call(server, tool, arguments=None, timeout=90):
        calls.update(server=server, tool=tool)
        return {"server": server, "tool": tool,
                "result": {"USD": "43.1"}, "is_error": False}
    monkeypatch.setattr(srv, "do_call", fake_call)
    out = srv.do_ask("dollar exchange rate today")
    assert out["mode"] == "search-fallback"
    assert out["executed"] is True
    assert calls["server"] == "tcmb-mcp"
    assert out["result"] == {"USD": "43.1"}
    _assert_steps_well_formed(out)
    assert out["steps"][1]["status"] == "ok"


def test_ask_fallback_needs_arguments(monkeypatch):
    """Fallback cannot invent arguments: it returns the top tool with
    its required parameters listed, and executes nothing."""

    class _StubSearch:
        def search(self, query, limit=8, location_country=None,
                   place_hint=None):
            return [{
                "server": "flixtrain-mcp", "country": "DE",
                "tool": "flixtrain_search_trains",
                "description": "Search FlixTrain departures",
                "params_required": ["from_city", "to_city", "date"],
                "on_pypi": True,
            }]

    monkeypatch.setattr(srv, "_search", lambda: _StubSearch())
    monkeypatch.setattr(srv, "do_call",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not execute")))
    out = srv.do_ask("قیمت بلیط قطار از برلین به مونیخ چقدره؟")
    assert out["mode"] == "search-fallback"
    assert out["executed"] is False
    assert out["routing"]["tool"] == "flixtrain_search_trains"
    assert "from_city" in out["needs_arguments"]
    assert "to_city" in out["needs_arguments"]
    _assert_steps_well_formed(out)
    assert out["steps"][1]["status"] == "skipped"
    assert out["steps"][1]["ms"] == 0
    assert out["sources"][0]["title"] == "flixtrain-mcp on GitHub"


def test_ask_empty_question():
    out = srv.do_ask("   ")
    assert "error" in out

# --- sources: URLs mined from results -------------------------------------

def test_ask_sources_mine_result_urls(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(A, "route_question", lambda q, c, p, **k: {
        "server": "tagesschau-mcp", "tool": "tagesschau_headlines",
        "arguments": {}, "reason": "news"})
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": "tagesschau-mcp", "tool": "tagesschau_headlines",
        "result": {"items": [
            {"title": "Breaking news",
             "url": "https://www.tagesschau.de/x.html"},
            {"title": "Second story",
             "url": "https://www.tagesschau.de/y.html"}]},
        "is_error": False})
    out = srv.do_ask("latest news germany")
    urls = {s["url"] for s in out["sources"]}
    assert "https://www.tagesschau.de/x.html" in urls
    assert "https://www.tagesschau.de/y.html" in urls
    titles = {s["title"] for s in out["sources"]}
    assert "Breaking news" in titles
    assert "tagesschau-mcp on GitHub" in titles


# --- per-request location hint --------------------------------------------

def _save_tr(monkeypatch):
    from all_mcp import location as loc_mod

    monkeypatch.setattr(
        loc_mod, "resolve_location",
        lambda: {"country": "TR", "city": None, "source": "file"})


def test_ask_hint_beats_saved_location(monkeypatch):
    """TR saved + DE per-request hint, no place names -> DE first."""
    _save_tr(monkeypatch)
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1], "result": {}, "is_error": False})
    without = srv.do_ask("قیمت بلیط اتوبوس")
    assert without["alternatives"][0]["country"] == "TR"
    assert without["location"]["source"] == "saved"
    with_hint = srv.do_ask("قیمت بلیط اتوبوس",
                            location={"country": "DE"})
    assert with_hint["alternatives"][0]["country"] == "DE"
    assert with_hint["location"] == {
        "country": "DE", "city": None, "source": "hint"}


def test_ask_hint_with_city(monkeypatch):
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1], "result": {}, "is_error": False})
    out = srv.do_ask("قیمت بلیط اتوبوس",
                     location={"country": "DE", "city": "Berlin"})
    assert out["alternatives"][0]["country"] == "DE"
    assert out["location"]["city"] == "Berlin"


def test_ask_hint_as_bare_string(monkeypatch):
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1], "result": {}, "is_error": False})
    out = srv.do_ask("قیمت بلیط اتوبوس", location="DE")
    assert out["location"]["country"] == "DE"
    assert out["alternatives"][0]["country"] == "DE"


def test_ask_explicit_place_beats_hint(monkeypatch):
    """Munich in the question + TR hint -> DE still wins."""
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1], "result": {}, "is_error": False})
    out = srv.do_ask("اتوبوس مونیخ به هامبورگ",
                     location={"country": "TR", "city": "Ankara"})
    assert out["alternatives"][0]["country"] == "DE"


def test_ask_hint_reaches_llm_prompt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    seen = {}

    def fake_route(question, catalog, provider, location=None):
        seen["location"] = location
        return {"server": "tcmb-mcp", "tool": "tcmb_get_rates",
                "arguments": {}, "reason": "x"}
    monkeypatch.setattr(A, "route_question", fake_route)
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1], "result": {}, "is_error": False})
    srv.do_ask("قیمت بلیط اتوبوس",
               location={"country": "DE", "city": "Berlin"})
    assert seen["location"] == "Germany (Berlin)"


def test_ask_invalid_location_is_clean_error():
    with pytest.raises(ValueError, match="Invalid location country"):
        srv.do_ask("hello", location={"country": "XX"})
    with pytest.raises(ValueError, match="location must be"):
        srv.do_ask("hello", location=42)


def test_build_prompt_location_line():
    _, user = A.build_prompt("bus ticket", "CATALOG",
                             location="Germany (Berlin)")
    assert "User location: Germany (Berlin)" in user
    _, plain = A.build_prompt("bus ticket", "CATALOG")
    assert "User location" not in plain


def test_stdio_ask_tool_text_has_steps_and_sources(monkeypatch):
    """The stdio ask tool's result text carries the same trace."""
    monkeypatch.setattr(srv, "do_call", lambda *a, **k: {
        "server": a[0], "tool": a[1],
        "result": {"USD": "43.1"}, "is_error": False})
    out = json.loads(srv.ask("dollar exchange rate today"))
    _assert_steps_well_formed(out)
    assert "tcmb-mcp/tcmb_get_rates ok" in out["trace"]
    assert out["sources"][0]["title"] == "tcmb-mcp on GitHub"


def test_stdio_ask_tool_invalid_location_is_error_text(monkeypatch):
    out = json.loads(srv.ask("hello", location={"country": "XX"}))
    assert "Invalid location country" in out["error"]
