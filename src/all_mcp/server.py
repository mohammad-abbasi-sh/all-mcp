"""all-mcp: ONE MCP server containing all of Mohammad Abbasi's MCPs.

Turkey catalog (32 servers) + Germany catalog (27) + bonbama-mcp —
60 servers, 324 tools — behind 6 tools. The user's own AI does the
detecting: it searches, picks, and calls. Exposing all 324 tools at
once would drown clients (context blowup, wrong picks), so the router
exposes only search + call + discovery — the standard router pattern.

How a question flows (one search + one call, no extra round trips):
1. `search_tools("قیمت بلیط قطار از برلین به مونیخ چقدره؟")`
   -> ranked tools with server, country and one-line descriptions.
2. `call_tool(server="flixtrain-mcp",
   tool="flixtrain_search_trains", arguments={...})`
   -> the real answer from the child server.

Or, in one step, `ask(question)`: with a routing LLM configured
(the user's own Gemini / OpenAI / Anthropic / OpenRouter key, or a
local Ollama), the router itself picks the server+tool+arguments,
executes, and explains its choice. Without any provider, `ask` falls
back to the top search result. `search_tools` + `call_tool` always
work with no key at all.

The same router also serves a local HTTP bridge for the website chat
UI: `all-mcp --http --port 8765` (localhost only, see http.py).

Local only: stdio, no backend, no account, no API keys. Child
servers run on the user's own machine via `uvx`. Session env vars
(HEPSIBURADA_SESSION, MIGROS_SESSION, ...) pass through from the
user's own MCP config and are never logged, printed, or stored.
Write actions still need explicit confirm=true (enforced by the
child servers). Location (USER_COUNTRY / set_location) never leaves
the user's machine.

Built by Mohammad Abbasi (mohammad-abbasi-sh) — https://almanibama.ir
· https://mabbasi.ir
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from . import child as _child
from . import ask as _ask
from . import index as _index
from . import location as _loc

mcp = FastMCP("all-mcp")

INSTRUCTIONS = """\
all-mcp — one MCP server for all 60 of Mohammad Abbasi's MCP servers
(Turkey 32, Germany 27, Bonbama 1; 324 tools). Two ways to answer a
question. (A) ask(question): one step — the router picks the
server+tool+arguments with a routing LLM if one is configured
(Gemini/OpenAI/Anthropic/OpenRouter key or local Ollama), else it
falls back to the top search result; use this when the user just
wants an answer. (B) search_tools FIRST with the user's question
(any language: EN/TR/DE/FA), then call_tool with the top-ranked
server+tool — one search + one call, no probing around. Results
carry the server's country (DE/TR/IR) and a one-line description: if
the top hits span countries and the question names no place, ask the
user which country they mean instead of guessing. Some servers are
not on PyPI yet (search results flag on_pypi=false) — calling one
returns a clear error, never a hang. Write actions (cart, favorites,
messages) need explicit confirm=true in the arguments. Location:
the user's saved country (USER_COUNTRY env or set_location) only
breaks ties when the question names no place; a per-request
location hint to ask() beats the saved location; explicit place names
in the question always win. Everything runs locally on the user's
machine; no account, no API key needed for search/call, no data
leaves the machine (ask only contacts a routing LLM when the user
configured one).\
"""

_DATA: dict | None = None
_SEARCH: _index.SearchIndex | None = None
_ROUTER = _child.Router()


def _data() -> dict:
    global _DATA
    if _DATA is None:
        _DATA = _index.load_index()
    return _DATA


def _search() -> _index.SearchIndex:
    global _SEARCH
    if _SEARCH is None:
        _SEARCH = _index.SearchIndex(_data())
    return _SEARCH


def _envelope() -> dict:
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "router": "all-mcp",
    }


def _ok(payload: dict) -> str:
    return json.dumps({**_envelope(), **payload}, ensure_ascii=False)


def _err(message: str, **extra) -> str:
    return _ok({"error": message, **extra})


# -- plain implementations (testable without the MCP layer) ----------

def do_search(query: str, limit: int = 8) -> dict:
    loc = _loc.resolve_location()
    country = loc["country"] if loc else None
    search = _search()
    results = search.search(query, limit=limit,
                            location_country=country)
    note = None
    if country and not search.names_place(query):
        note = (f"Results re-ranked for saved location {country} "
                "(the question named no place).")
    return {
        "query": query,
        "location": loc,
        "ranking_note": note,
        "count": len(results),
        "results": results,
        "hint": ("Call call_tool with the top result's server+tool. "
                 "If results span countries and the question named no "
                 "place, ask the user which country they mean."),
    }


def do_call(server: str, tool: str, arguments: dict | None = None,
            timeout: int = 90) -> dict:
    data = _data()
    entry = _index.get_server(data, server)
    if entry is None:
        return {"error": f"Unknown server {server!r}. "
                "Use list_servers or search_tools to find server ids."}
    names = {t["name"] for t in entry.get("tools", [])}
    if tool not in names:
        return {"error": f"Server {server!r} has no tool {tool!r}. "
                f"Its tools: {sorted(names)}"}
    if entry.get("on_pypi") is False:
        return {
            "error": f"Server {server!r} is not published on PyPI yet, "
            "so it cannot be started. It will work once its package "
            "is published.",
            "server": server, "tool": tool,
            "reason": "not_on_pypi",
        }
    if not 5 <= timeout <= 300:
        return {"error": "timeout must be between 5 and 300 seconds"}
    package = entry.get("pypi") or server
    try:
        raw = _ROUTER.call(package, tool, arguments, timeout=timeout)
    except _child.RouterError as e:
        return {"error": str(e), "server": server, "tool": tool,
                "reason": e.reason}
    texts = [
        c.get("text", "") for c in raw.get("content", [])
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    joined = "\n".join(texts)
    try:
        result = json.loads(joined) if joined.strip() else None
    except json.JSONDecodeError:
        result = joined
    return {
        "server": server, "tool": tool,
        "country": entry.get("country"),
        "is_error": raw.get("is_error", False),
        "result": result,
    }


def do_list_servers(catalog: str | None = None,
                    country: str | None = None,
                    category: str | None = None) -> dict:
    servers = _index.list_servers(_data(), catalog=catalog,
                                  country=country, category=category)
    return {"count": len(servers), "servers": servers}


def do_health() -> dict:
    data = _data()
    counts = data.get("counts", {})
    on_pypi = sum(1 for s in data.get("servers", [])
                  if s.get("on_pypi") is True)
    pending = sorted(s["id"] for s in data.get("servers", [])
                     if s.get("on_pypi") is False)
    import shutil
    try:
        llm_provider = _ask.select_provider()
        llm_error = None
    except _ask.AskError as e:
        llm_provider, llm_error = None, str(e)
    return {
        "status": "ok",
        "servers": counts.get("servers", len(data.get("servers", []))),
        "tools": counts.get("tools", _search().tool_count),
        "by_catalog": counts.get("by_catalog", {}),
        "index_generated_at": data.get("generated_at"),
        "on_pypi": on_pypi,
        "not_on_pypi": pending,
        "uvx_available": shutil.which("uvx") is not None,
        "cached_child_servers": _ROUTER.cached_servers(),
        "location": _loc.resolve_location(),
        "llm_provider": llm_provider,
        "llm_configured": llm_provider is not None,
        "llm_error": llm_error,
    }


def _alternatives(results: list[dict]) -> list[dict]:
    return [{
        "server": r["server"], "country": r["country"],
        "tool": r["tool"], "description": r["description"],
        "params_required": r["params_required"],
        "on_pypi": r["on_pypi"],
    } for r in results]


def _normalize_location(location) -> tuple[str | None, str | None]:
    """Normalize a per-request location hint to (country, city).

    Accepts {"country": "DE", "city": "Berlin"} or a bare "DE"
    string; None/empty means no hint. Raises ValueError on an
    invalid country code (valid: DE, TR, IR).
    """
    if location is None:
        return None, None
    if isinstance(location, str):
        country, city = location, None
    elif isinstance(location, dict):
        country, city = location.get("country"), location.get("city")
    else:
        raise ValueError(
            "location must be an object {country, city?} or a "
            "country code string")
    if country is None:
        return None, None
    country = str(country).strip().upper()
    if country not in _loc.VALID_COUNTRIES:
        raise ValueError(
            f"Invalid location country {country!r} — use DE, TR or IR")
    city = str(city).strip() if city else None
    return country, city or None


def _step(server: str, tool: str, status: str, ms: int) -> dict:
    return {"server": server, "tool": tool, "status": status, "ms": ms}


def _iter_urls(obj, _depth: int = 0):
    """Yield (title, url) pairs mined from a tool result.

    Walks dicts/lists; a URL inside an object with a title/name uses
    it, otherwise the domain is the title. Depth-capped, deduped by
    the caller.
    """
    from urllib.parse import urlparse

    if _depth > 4:
        return
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")):
            try:
                domain = urlparse(obj).netloc or obj
            except ValueError:
                domain = obj
            yield domain, obj
        return
    if isinstance(obj, dict):
        title = obj.get("title") or obj.get("name") or ""
        for value in obj.values():
            if isinstance(value, str) and value.startswith(
                    ("http://", "https://")):
                try:
                    domain = urlparse(value).netloc or value
                except ValueError:
                    domain = value
                yield (str(title) or domain), value
            else:
                yield from _iter_urls(value, _depth + 1)
        return
    if isinstance(obj, list):
        for item in obj:
            yield from _iter_urls(item, _depth + 1)


def _sources_for(server_id: str | None, result) -> list[dict]:
    """Citation links: the server's repo + URLs mined from the result
    (max 6 total)."""
    sources: list[dict] = []
    seen: set[str] = set()
    if server_id:
        entry = _index.get_server(_data(), server_id)
        if entry and entry.get("repo"):
            sources.append({"title": f"{server_id} on GitHub",
                            "url": entry["repo"]})
            seen.add(entry["repo"])
    if result is not None:
        for title, url in _iter_urls(result):
            if url in seen:
                continue
            seen.add(url)
            sources.append({"title": title, "url": url})
            if len(sources) >= 6:
                break
    return sources


def _trace(steps: list[dict], sources: list[dict]) -> str:
    """Compact human-readable trace: one line per step + sources."""
    lines = []
    for s in steps:
        lines.append(f"{s['server']}/{s['tool']} {s['status']} "
                     f"({s['ms']}ms)")
    for src in sources:
        lines.append(f"Source: {src['title']} — {src['url']}")
    return "\n".join(lines)


def do_ask(question: str, location=None) -> dict:
    """One-step question answering.

    With a routing LLM configured: the model picks server+tool+
    arguments, the router executes and explains the choice. Without
    any provider: falls back to the top search result — auto-executed
    when it needs no arguments, otherwise returned with the required
    parameters listed so the caller can supply them (or call
    call_tool directly). Alternatives are always included.

    location: optional per-request hint {"country", "city?"} (or a
    bare country code) — the chat UI sends it from its browser
    memory. Ranking priority: explicit place names in the question >
    this hint > saved location (USER_COUNTRY env / set_location) >
    none. A city hint adds place-score weight in the search (only
    when the question names no place itself), and the effective
    location is passed to the routing LLM.

    Every outcome carries `steps` (the routing trace: one entry per
    routing/execution attempt, {server, tool, status ok|failed|
    skipped, ms}), `sources` (citation links: the server repo + URLs
    mined from the result), and `trace` (the same, one line per step,
    for human readers and the stdio tool result text).
    """
    import time as _time

    t_start = _time.monotonic()
    hint_country, hint_city = _normalize_location(location)
    saved = _loc.resolve_location()
    eff_country = hint_country or (saved["country"] if saved else None)
    eff_city = hint_city or (saved.get("city") if saved else None)
    loc_used = {
        "country": eff_country, "city": eff_city,
        "source": ("hint" if hint_country
                   else "saved" if saved else None),
    }
    loc_label = None
    if eff_country:
        loc_label = _loc.COUNTRY_NAMES.get(eff_country, eff_country)
        if eff_city:
            loc_label += f" ({eff_city})"
    if not question or not question.strip():
        return {"error": "question must not be empty",
                "location": loc_used,
                "steps": [], "sources": [], "trace": ""}
    search = _search()
    results = search.search(question, limit=5,
                            location_country=eff_country,
                            place_hint=eff_city)
    alternatives = _alternatives(results[:3])
    try:
        provider = _ask.select_provider()
    except _ask.AskError as e:
        return {"error": str(e), "mode": "error",
                "location": loc_used,
                "alternatives": alternatives,
                "steps": [], "sources": [], "trace": ""}
    if provider is None:
        route_ms = int((_time.monotonic() - t_start) * 1000)
        steps = [_step("all-mcp", "route", "ok", route_ms)]
        if not results:
            return {"error": "No tool in the catalog matches this "
                    "question.", "mode": "search-fallback",
                    "location": loc_used,
                    "alternatives": [],
                    "steps": steps, "sources": [],
                    "trace": _trace(steps, [])}
        for top in results:
            if top["on_pypi"] is False:
                continue
            needed = top["params_required"]
            if needed:
                continue  # needs arguments; try the next result
            t_exec = _time.monotonic()
            out = do_call(top["server"], top["tool"], {})
            exec_ms = int((_time.monotonic() - t_exec) * 1000)
            failed = bool(out.get("error") or out.get("is_error"))
            steps.append(_step(top["server"], top["tool"],
                               "failed" if failed else "ok", exec_ms))
            sources = _sources_for(top["server"], out.get("result"))
            return {
                "mode": "search-fallback", "executed": True,
                "location": loc_used,
                "routing": {
                    "server": top["server"], "tool": top["tool"],
                    "reason": "Top search result (no routing LLM "
                              "configured)."},
                "result": out.get("result"),
                "is_error": out.get("is_error", False),
                "error": out.get("error"),
                "alternatives": alternatives,
                "steps": steps, "sources": sources,
                "trace": _trace(steps, sources),
            }
        top = next((r for r in results if r["on_pypi"] is not False),
                   results[0])
        steps.append(_step(top["server"], top["tool"], "skipped", 0))
        sources = _sources_for(top["server"], None)
        return {
            "mode": "search-fallback", "executed": False,
            "location": loc_used,
            "routing": {"server": top["server"], "tool": top["tool"],
                        "reason": "Top search result (no routing LLM "
                                  "configured)."},
            "needs_arguments": top["params_required"],
            "note": ("This tool needs arguments the question did not "
                     "give as values. Ask the user for them, then "
                     "call call_tool — or set a routing provider key "
                     "(GEMINI_API_KEY, OPENAI_API_KEY, "
                     "ANTHROPIC_API_KEY, OPENROUTER_API_KEY) or "
                     "LLM_PROVIDER=ollama for one-step answers."),
            "alternatives": alternatives,
            "steps": steps, "sources": sources,
            "trace": _trace(steps, sources),
        }
    try:
        routing = _ask.route_question(
            question, _ask.build_catalog_text(_data()), provider,
            location=loc_label)
    except _ask.AskError as e:
        route_ms = int((_time.monotonic() - t_start) * 1000)
        steps = [_step("all-mcp", "route", "failed", route_ms)]
        return {"error": str(e), "mode": "llm", "provider": provider,
                "location": loc_used,
                "alternatives": alternatives, "steps": steps,
                "sources": [], "trace": _trace(steps, [])}
    route_ms = int((_time.monotonic() - t_start) * 1000)
    if not routing.get("server") or not routing.get("tool"):
        steps = [_step("all-mcp", "route", "ok", route_ms)]
        return {"mode": "llm", "provider": provider, "executed": False,
                "location": loc_used,
                "reason": routing.get("reason", ""),
                "error": "No tool in the catalog can answer this "
                         "question.",
                "alternatives": alternatives, "steps": steps,
                "sources": [], "trace": _trace(steps, [])}
    entry = _index.get_server(_data(), routing["server"])
    if entry is None or routing["tool"] not in {
            t["name"] for t in entry["tools"]}:
        steps = [_step("all-mcp", "route", "failed", route_ms)]
        detail = ("unknown server" if entry is None
                  else "unknown tool")
        return {"error": f"The routing model chose {detail} "
                f"{routing['server']!r}/{routing['tool']!r}.",
                "mode": "llm", "provider": provider,
                "location": loc_used,
                "alternatives": alternatives, "steps": steps,
                "sources": [], "trace": _trace(steps, [])}
    steps = [_step("all-mcp", "route", "ok", route_ms)]
    if entry.get("on_pypi") is False:
        steps.append(_step(routing["server"], routing["tool"],
                           "skipped", 0))
        sources = _sources_for(routing["server"], None)
        return {"error": f"The right server ({routing['server']}) "
                "exists but is not published on PyPI yet, so it "
                "cannot run.",
                "mode": "llm", "provider": provider,
                "location": loc_used,
                "alternatives": alternatives, "steps": steps,
                "sources": sources, "trace": _trace(steps, sources)}
    t_exec = _time.monotonic()
    out = do_call(routing["server"], routing["tool"],
                  routing.get("arguments") or {})
    exec_ms = int((_time.monotonic() - t_exec) * 1000)
    failed = bool(out.get("error") or out.get("is_error"))
    steps.append(_step(routing["server"], routing["tool"],
                       "failed" if failed else "ok", exec_ms))
    sources = _sources_for(routing["server"], out.get("result"))
    return {
        "mode": "llm", "provider": provider, "executed": True,
        "location": loc_used,
        "routing": {"server": routing["server"],
                    "tool": routing["tool"],
                    "reason": routing.get("reason", "")},
        "result": out.get("result"),
        "is_error": out.get("is_error", False),
        "error": out.get("error"),
        "alternatives": alternatives,
        "steps": steps, "sources": sources,
        "trace": _trace(steps, sources),
    }


# -- MCP tools --------------------------------------------------------

@mcp.tool()
def search_tools(query: str, limit: int = 8) -> str:
    """Find the right MCP tool(s) for a question, in any language.

    query: the user's question (EN/TR/DE/FA), e.g. "قیمت بلیط قطار
    از برلین به مونیخ چقدره؟" or "dollar exchange rate today".
    limit 1..20 (default 8). Returns ranked tools with server id,
    country (DE/TR/IR), one-line descriptions and required params.
    Call this FIRST, then call_tool with the top result. If results
    span countries and the question names no place, ask the user
    which country they mean."""
    try:
        return _ok(do_search(query, limit=limit))
    except ValueError as e:
        return _err(str(e))


@mcp.tool()
def call_tool(server: str, tool: str, arguments: dict | None = None,
              timeout: int = 90) -> str:
    """Run one tool on one child MCP server and return its real result.

    server: server id from search_tools (e.g. "flixtrain-mcp").
    tool: tool name from search_tools. arguments: the tool's
    arguments as an object (use params_required from search results;
    omit for tools with no required params). timeout 5..300s
    (default 90). Write actions (cart, favorites, messages) need
    explicit confirm=true inside arguments. If the server is not on
    PyPI yet, returns a clear error instead of hanging."""
    try:
        out = do_call(server, tool, arguments, timeout=timeout)
    except Exception as e:  # never crash the router on a child failure
        out = {"error": f"Router error: {e}", "server": server,
               "tool": tool, "reason": "router_error"}
    return _ok(out)


@mcp.tool()
def ask(question: str, location: dict | None = None) -> str:
    """Answer a question in one step: route it to the right MCP tool
    and run it. With a routing LLM configured (the user's own
    GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY /
    OPENROUTER_API_KEY, or LLM_PROVIDER=ollama for local routing),
    the model picks the server+tool+arguments; without any provider,
    the top search result is used instead. location: optional
    per-request hint {"country": "DE"|"TR"|"IR", "city"?} — beats the
    saved location, loses to place names in the question. Returns
    the tool result plus a one-line routing explanation, the
    routing trace (steps: one entry per routing/execution attempt,
    plus a compact one-line-per-step `trace`), citation links
    (sources), and alternatives. The question is sent to the
    routing provider only when one is configured; search_tools +
    call_tool never need a key."""
    try:
        return _ok(do_ask(question, location=location))
    except ValueError as e:  # e.g. invalid location country
        return _err(str(e))
    except Exception as e:  # never crash the router on a routing failure
        return _err(f"ask failed: {e}")


@mcp.tool()
def list_servers(catalog: str | None = None,
                 country: str | None = None,
                 category: str | None = None) -> str:
    """List the MCP servers in the index, optionally filtered.

    catalog: "turkey", "germany" or "bonbama". country: "DE", "TR"
    or "IR". category: e.g. "transport", "finance", "news". Omit all
    filters to list everything (60 servers)."""
    return _ok(do_list_servers(catalog, country, category))


@mcp.tool()
def set_location(country: str, city: str | None = None) -> str:
    """Save the user's country (and optional city) for location-first
    routing. Saved to a LOCAL file only (~/.config/all-mcp/
    location.json) — it never leaves this machine. country: "DE"
    (Germany), "TR" (Türkiye) or "IR" (Iran). Takes effect when no
    USER_COUNTRY env var is set in the MCP config (the env var wins
    if both exist). Explicit place names in a question always beat
    the saved location."""
    try:
        return _ok(_loc.save_location(country, city))
    except ValueError as e:
        return _err(str(e))


@mcp.tool()
def meta_health() -> str:
    """Router status: index counts, generation time, how many servers
    are on PyPI (and which are still pending), whether uvx is
    available, cached child servers, and the current location."""
    try:
        return _ok(do_health())
    except Exception as e:
        return _err(f"Health check failed: {e}")


def main() -> None:
    """Entry point: stdio MCP server by default (Claude Desktop path);
    --http serves the local HTTP bridge for the website chat UI."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="all-mcp",
        description="One MCP server for all of Mohammad Abbasi's MCPs.")
    ap.add_argument("--http", action="store_true",
                    help="serve the local HTTP bridge (localhost only) "
                         "instead of the stdio MCP server")
    ap.add_argument("--port", type=int, default=8765,
                    help="HTTP bridge port (default 8765)")
    args = ap.parse_args()
    if args.http:
        from .http import serve

        serve(port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    import sys

    sys.exit(main())
