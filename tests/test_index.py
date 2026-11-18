"""Offline tests for the generated tool index.

Run with:  python -m pytest tests/test_index.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from all_mcp.index import load_index  # noqa: E402

DATA = load_index()
SERVERS = {s["id"]: s for s in DATA["servers"]}
PENDING_PYPI = {"paribu-mcp", "diyanet-mcp", "osym-mcp",
                "heise-mcp", "zeit-mcp", "welt-mcp"}


def test_counts():
    assert DATA["counts"]["servers"] == 60
    assert DATA["counts"]["tools"] == 324
    by_cat = DATA["counts"]["by_catalog"]
    assert by_cat["turkey"] == {"servers": 32, "tools": 186}
    assert by_cat["germany"] == {"servers": 27, "tools": 132}
    assert by_cat["bonbama"] == {"servers": 1, "tools": 6}


def test_no_duplicate_servers_or_tools():
    assert len(SERVERS) == 60
    for server in DATA["servers"]:
        names = [t["name"] for t in server["tools"]]
        assert len(names) == len(set(names)), server["id"]


def test_every_server_has_metadata():
    for server in DATA["servers"]:
        assert server["id"], server
        assert server["pypi"], server["id"]
        assert server["country"] in ("TR", "DE", "IR"), server["id"]
        assert server["catalog"] in ("turkey", "germany", "bonbama")
        assert server["category"], server["id"]
        assert server["title"], server["id"]
        assert server["desc"], server["id"]
        assert server["tools"], server["id"]
        assert server["place_keywords"], server["id"]
        for tool in server["tools"]:
            assert tool["name"], server["id"]
            assert tool["desc"], (server["id"], tool["name"])


def test_catalog_country_consistency():
    for server in DATA["servers"]:
        expected = {"turkey": "TR", "germany": "DE", "bonbama": "IR"}
        assert server["country"] == expected[server["catalog"]]


def test_pending_pypi_servers_flagged():
    for sid in PENDING_PYPI:
        assert SERVERS[sid]["on_pypi"] is False, sid
    assert SERVERS["bonbama-mcp"]["on_pypi"] is True
    assert SERVERS["tcmb-mcp"]["on_pypi"] is True
    assert SERVERS["flixtrain-mcp"]["on_pypi"] is True
    flagged = {s["id"] for s in DATA["servers"]
               if s.get("on_pypi") is False}
    assert flagged == PENDING_PYPI


def test_flixtrain_ticket_keyword():
    kws = SERVERS["flixtrain-mcp"]["keywords"]
    assert "ticket" in kws and "بلیط" in kws


def test_bonbama_tools():
    tools = {t["name"] for t in SERVERS["bonbama-mcp"]["tools"]}
    assert tools == {"bb_search_businesses", "bb_get_business",
                     "bb_list_categories", "bb_search_discounts",
                     "bb_search_articles", "bb_health"}


def test_index_matches_platform_catalogs():
    """Independent cross-check: every server id in the local platform
    working copies must be in the index and vice versa."""
    import gen_index as G  # noqa: E402

    tr = G.parse_platform(G.fetch_text(G.TR_HTML, None))
    de = G.parse_platform(G.fetch_text(G.DE_HTML, None))
    catalog_ids = {s["id"] for s in tr} | {s["id"] for s in de}
    index_ids = {s["id"] for s in DATA["servers"]
                 if s["catalog"] in ("turkey", "germany")}
    assert catalog_ids == index_ids
    tr_tools = sum(len(s.get("tools", [])) for s in tr)
    de_tools = sum(len(s.get("tools", [])) for s in de)
    assert tr_tools == 186 and de_tools == 132


def test_gen_is_idempotent(tmp_path, monkeypatch):
    """Regenerating into a temp file twice: second run changes nothing."""
    import gen_index as G  # noqa: E402

    out = tmp_path / "tools_index.json"
    monkeypatch.setattr(G, "OUT_PATH", out)
    assert G.main(["--no-pypi-check"]) == 0
    first = out.read_text(encoding="utf-8")
    first_data = json.loads(first)
    assert first_data["counts"]["servers"] == 60
    assert first_data["counts"]["tools"] == 324
    assert G.main(["--no-pypi-check"]) == 0
    assert out.read_text(encoding="utf-8") == first
