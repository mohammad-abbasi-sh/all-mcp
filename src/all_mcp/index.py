"""The tool index: loading tools_index.json and multilingual search.

Search scoring (all local, no network):
- topical score: per query token (expanded through the multilingual
  concept map), the best match across the tool name (6/4), its
  descriptions in any language (3/2), and its server's title/desc/
  category (1.5/1). Exact beats prefix.
- place score: +4 per query token naming a place the server covers
  (city/country keywords, e.g. München/مونیخ/برلین for DE servers),
  capped at +8. This is what makes "bus Munich->Hamburg" rank
  Germany transport above Turkey transport.
- tiering: results with a topical match always rank above place-only
  matches, so a train question doesn't surface weather tools just
  because they share a city. Place-only results still appear (after
  the topical ones) so the client LLM can see the country's options
  and disambiguate.

Search results always carry the server's country + one-line
description, so the client LLM can itself disambiguate ("this is a
Turkish bus server — the user asked about Munich, so prefer the
German train option, or ask").
"""
from __future__ import annotations

import json
from pathlib import Path

from .concepts import (CATEGORY_TOPICS, expand, name_tokens,
                       token_match, tokenise)

INDEX_PATH = Path(__file__).resolve().parent / "tools_index.json"

W_NAME_EXACT = 6.0
W_NAME_PREFIX = 4.0
W_DESC_EXACT = 3.0
W_DESC_PREFIX = 2.0
W_SERVER_EXACT = 1.5
W_SERVER_PREFIX = 1.0
W_PLACE_TOKEN = 4.0
PLACE_CAP = 8.0
W_CATEGORY = 2.0  # question topic matches the server's category
W_LOCATION = 3.0  # saved-location boost (tier members only)
LOCATION_TOPICAL_FLOOR = 3.0  # a location-tier result needs a real
# topical match, not noise — otherwise a weak same-country match
# would beat a strong cross-country one
MAX_PER_SERVER = 2  # results per server: surface different servers,
# not five tools from one, so both countries' options appear


def load_index(path: Path | str | None = None) -> dict:
    """Load the generated tools_index.json (raises if missing)."""
    p = Path(path) if path else INDEX_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"Tool index not found at {p}. Regenerate it with: "
            f"python3 gen_index.py"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def get_server(data: dict, server_id: str) -> dict | None:
    for server in data.get("servers", []):
        if server["id"] == server_id:
            return server
    return None


def list_servers(data: dict, catalog: str | None = None,
                 country: str | None = None,
                 category: str | None = None) -> list[dict]:
    out = []
    for s in data.get("servers", []):
        if catalog and s.get("catalog") != catalog:
            continue
        if country and s.get("country") != country:
            continue
        if category and s.get("category") != category:
            continue
        out.append({
            "id": s["id"], "catalog": s.get("catalog"),
            "country": s.get("country"), "category": s.get("category"),
            "title": s.get("title"), "title_local": s.get("title_local"),
            "description": s.get("desc"),
            "description_fa": s.get("desc_fa"),
            "tool_count": len(s.get("tools", [])),
            "pypi": s.get("pypi"), "on_pypi": s.get("on_pypi"),
            "repo": s.get("repo"),
        })
    return out


class _ToolEntry:
    __slots__ = ("server", "tool", "name_toks", "desc_toks",
                 "server_toks", "place_toks")

    def __init__(self, server: dict, tool: dict) -> None:
        self.server = server
        self.tool = tool
        self.name_toks = set(name_tokens(tool["name"]))
        desc_text = " ".join(filter(None, [
            tool.get("desc", ""), tool.get("desc_fa", ""),
            tool.get("desc_local", ""),
        ]))
        self.desc_toks = set(tokenise(desc_text))
        for kw in server.get("keywords", []) or []:
            self.desc_toks.update(tokenise(kw))
        server_text = " ".join(filter(None, [
            server.get("id", ""), server.get("title", ""),
            server.get("title_local", ""), server.get("desc", ""),
            server.get("desc_fa", ""), server.get("desc_local", ""),
            server.get("category", ""),
        ]))
        self.server_toks = set(tokenise(server_text))
        self.server_toks.update(name_tokens(server.get("id", "")))
        self.place_toks = set()
        for kw in server.get("place_keywords", []) or []:
            self.place_toks.update(tokenise(kw))


class SearchIndex:
    """Precomputed token sets for all tools; search is pure Python."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.entries: list[_ToolEntry] = []
        self.all_place_toks: set[str] = set()
        for server in data.get("servers", []):
            for tool in server.get("tools", []):
                entry = _ToolEntry(server, tool)
                self.entries.append(entry)
                self.all_place_toks.update(entry.place_toks)

    @property
    def server_count(self) -> int:
        return len(self.data.get("servers", []))

    @property
    def tool_count(self) -> int:
        return len(self.entries)

    def names_place(self, query: str) -> bool:
        """True if the query names any place the index covers."""
        return any(
            token_match(qt, self.all_place_toks) is not None
            for qt in tokenise(query)
        )

    def search(self, query: str, limit: int = 8,
               location_country: str | None = None,
               place_hint: str | None = None) -> list[dict]:
        """Rank tools for a query.

        location_country: the user's saved country (DE/TR/IR) or None.
        place_hint: a city hint (e.g. from a per-request location) —
        adds place-score weight for servers covering that city, but
        ONLY when the query names no place itself.
        Priority: explicit place names in the query beat the saved
        location and the hint; the saved location / hint only
        re-rank same-country servers first when the query names no
        place at all (and only for results with a real topical
        match). With no cue and no location, both countries' options
        are returned ranked.
        """
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        query_tokens = tokenise(query)
        if not query_tokens:
            return []
        explicit_place = any(
            token_match(qt, self.all_place_toks) is not None
            for qt in query_tokens
        )
        use_location = (
            location_country if not explicit_place else None
        )
        # The city hint is ignored when the question names its own
        # places — explicit beats hint, always.
        hint_toks = (set(tokenise(place_hint))
                     if place_hint and not explicit_place else set())
        expanded = [expand(qt) for qt in query_tokens]
        scored: list[tuple[int, float, float, int, str, str, dict]] = []
        for entry in self.entries:
            topical = 0.0
            matched = 0
            for forms in expanded:
                best = 0.0
                for form in forms:
                    m = token_match(form, entry.name_toks)
                    if m == "exact":
                        best = max(best, W_NAME_EXACT)
                    elif m == "prefix":
                        best = max(best, W_NAME_PREFIX)
                    m = token_match(form, entry.desc_toks)
                    if m == "exact":
                        best = max(best, W_DESC_EXACT)
                    elif m == "prefix":
                        best = max(best, W_DESC_PREFIX)
                    m = token_match(form, entry.server_toks)
                    if m == "exact":
                        best = max(best, W_SERVER_EXACT)
                    elif m == "prefix":
                        best = max(best, W_SERVER_PREFIX)
                if best > 0:
                    matched += 1
                topical += best
            place_hits = sum(
                1 for qt in query_tokens
                if token_match(qt, entry.place_toks) is not None
            )
            hint_hits = sum(
                1 for ht in hint_toks
                if token_match(ht, entry.place_toks) is not None
            )
            place = min(PLACE_CAP,
                        W_PLACE_TOKEN * (place_hits + hint_hits))
            cat_topics = CATEGORY_TOPICS.get(
                entry.server.get("category", ""), frozenset())
            cat = W_CATEGORY if any(
                forms & cat_topics for forms in expanded
            ) else 0.0
            in_loc_tier = bool(
                use_location
                and entry.server.get("country") == use_location
                and topical >= LOCATION_TOPICAL_FLOOR
            )
            loc = W_LOCATION if in_loc_tier else 0.0
            total = topical + place + cat + loc
            if total <= 0:
                continue
            scored.append((
                0 if in_loc_tier else 1,
                -total, -topical, -matched,
                entry.server["id"], entry.tool["name"],
                _result(entry, round(total, 1), round(topical, 1),
                        round(place, 1), round(cat, 1), round(loc, 1)),
            ))
        scored.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[4], r[5]))
        out: list[dict] = []
        per_server: dict[str, int] = {}
        for row in scored:
            sid = row[4]
            if per_server.get(sid, 0) >= MAX_PER_SERVER:
                continue
            per_server[sid] = per_server.get(sid, 0) + 1
            out.append(row[6])
            if len(out) >= limit:
                break
        return out


def _result(entry: _ToolEntry, total: float, topical: float,
            place: float, cat: float, loc: float) -> dict:
    server, tool = entry.server, entry.tool
    params = tool.get("params")
    required = None
    if isinstance(params, dict):
        required = list(params.get("required") or [])
    return {
        "server": server["id"],
        "country": server.get("country"),
        "catalog": server.get("catalog"),
        "category": server.get("category"),
        "tool": tool["name"],
        "score": total,
        "topical_score": topical,
        "place_score": place,
        "category_score": cat,
        "location_boost": loc,
        "server_title": server.get("title"),
        "description": tool.get("desc") or server.get("desc"),
        "description_fa": tool.get("desc_fa") or server.get("desc_fa"),
        "description_local": (tool.get("desc_local")
                              or server.get("desc_local")),
        "params_required": required,
        "params_known": params is not None,
        "on_pypi": server.get("on_pypi"),
    }
