#!/usr/bin/env python3
"""Regenerate the all-mcp tool index (src/all_mcp/tools_index.json).

Sources (local working copies by default — the factory crons update
them when a new server lands, then call this script):
  ../turkey-mcp/platform/index.html   (SERVERS array)
  ../germany-mcp/platform/index.html  (SERVERS array)
  ../bonbama-mcp/src/bonbama_mcp/server.py  (bb_* tool docstrings)

Cron-friendly by design (standing rule: every new server must update
the meta project):
  - fully non-interactive, no prompts, no stdin reads
  - idempotent: exits 0 and leaves the file untouched when the
    catalog is unchanged (payload compared, timestamp excluded)
  - atomic write (temp file + rename) so a killed run never leaves
    a partial index
  - short summary on stdout: servers/tools indexed, what changed
  - exit 0 on success (changed or not); exit 2 only on hard failure
    (a catalog unreadable)

Cron command (run from anywhere; paths resolve relative to this file):
  python3 ~/workspace/github-profile/projects/all-mcp/gen_index.py

Optional flags:
  --live        fetch the platform pages from github.io instead of
                the local working copies
  --probe       enrich tools with real inputSchemas by spawning each
                on-PyPI server via uvx and calling tools/list (slow:
                ~1-2 min per server on first run; failures are
                recorded, never fatal). Probe results are MERGED into
                the index and preserved across later plain runs.
  --no-pypi-check
                skip the PyPI availability check (keep previous
                on_pypi flags). The check itself degrades gracefully:
                on any network error the previous flag is kept.
  --verbose     one progress line per server during --probe
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

TR_HTML = os.environ.get("ALL_MCP_TR_HTML",
                         str(PROJECTS / "turkey-mcp" / "platform"
                             / "index.html"))
DE_HTML = os.environ.get("ALL_MCP_DE_HTML",
                         str(PROJECTS / "germany-mcp" / "platform"
                             / "index.html"))
BONBAMA_PY = os.environ.get(
    "ALL_MCP_BONBAMA_PY",
    str(PROJECTS / "bonbama-mcp" / "src" / "bonbama_mcp" / "server.py"))
OUT_PATH = Path(os.environ.get("ALL_MCP_OUT",
                               str(ROOT / "src" / "all_mcp"
                                   / "tools_index.json")))
TR_LIVE = "https://mohammad-abbasi-sh.github.io/turkey-mcp/"
DE_LIVE = "https://mohammad-abbasi-sh.github.io/germany-mcp/"

# Place keywords per country: city + country names in EN/DE/TR/FA.
# Attached to every server of the catalog so queries naming a place
# rank that country's servers above the other country's.
DE_PLACES = [
    "Deutschland", "Germany", "Almanya", "آلمان", "Münih",
    "Berlin", "برلین", "München", "Munich", "مونیخ",
    "Hamburg", "هامبورگ", "Köln", "Cologne", "کلن",
    "Frankfurt", "فرانکفورت", "Stuttgart", "اشتوتگارت",
    "Düsseldorf", "دوسلدورف", "Dortmund", "دورتموند",
    "Essen", "اسن", "Leipzig", "لایپزیگ", "Bremen", "برمن",
    "Dresden", "درسدن", "Hannover", "هانوفر",
    "Nürnberg", "Nuremberg", "نورنبرگ", "Duisburg", "دویسبورگ",
    "Bochum", "بوخوم", "Wuppertal", "ووپرتال",
    "Bielefeld", "بیله‌فلد", "Bonn", "بن", "Münster", "مونستر",
    "Karlsruhe", "کارلسروهه", "Mannheim", "مانهایم",
    "Augsburg", "آگسبورگ", "Wiesbaden", "ویسبادن",
    "Mönchengladbach", "مونشن‌گلادباخ", "Gelsenkirchen", "گلزن‌کیرشن",
    "Braunschweig", "براونشوایگ", "Chemnitz", "کمنیتس",
    "Kiel", "کیل", "Aachen", "آخن", "Halle", "هاله",
    "Magdeburg", "ماگدبورگ", "Freiburg", "فرایبورگ",
    "Krefeld", "کرفلد", "Lübeck", "لوبک",
    "Oberhausen", "اوبرهاوزن", "Erfurt", "ارفورت",
    "Mainz", "ماینتس", "Rostock", "روستوک", "Kassel", "کاسل",
    "Hagen", "هاگن", "Hamm", "هام", "Saarbrücken", "زاربروکن",
    "Mülheim", "مولهایم", "Potsdam", "پوتسدام",
    "Ludwigshafen", "لودویگسهافن", "Oldenburg", "اولدنبورگ",
    "Leverkusen", "لورکوزن", "Osnabrück", "اوزنابروک",
    "Solingen", "زولینگن", "Heidelberg", "هایدلبرگ",
    "Herne", "هرنه", "Neuss", "نویس", "Darmstadt", "دارمشتات",
    "Paderborn", "پادربورن", "Regensburg", "رگنسبورگ",
    "Ingolstadt", "اینگولشتات", "Würzburg", "وورتسبورگ",
    "Fürth", "فورث", "Wolfsburg", "ولفسبورگ", "Ulm", "اولم",
    "Heilbronn", "هایلبرون", "Pforzheim", "پفورتسهایم",
    "Göttingen", "گوتینگن", "Bottrop", "بوتروپ", "Trier", "تریر",
    "Recklinghausen", "رکلینگ‌هاوزن", "Bremerhaven", "برمرهافن",
    "Koblenz", "کوبلنتس", "Bergisch Gladbach", "برگیش گلادباخ",
    "Jena", "ینا", "Remscheid", "رمشاید", "Erlangen", "ارلانگن",
    "Moers", "مورس", "Siegen", "زیگن", "Hildesheim", "هیلدسهایم",
    "Salzgitter", "زالتسگیتر", "Cottbus", "کوتبوس",
    "Kaiserslautern", "کایزرسلاترن", "Gütersloh", "گوترسلو",
    "Schwerin", "شورین", "Iserlohn", "ایزرلون", "Düren", "دورن",
]
TR_PLACES = [
    "Türkiye", "Turkey", "ترکیه",
    "İstanbul", "Istanbul", "استانبول", "Ankara", "آنکارا",
    "İzmir", "Izmir", "ازمیر", "Antalya", "آنتالیا",
    "Bursa", "بورسا", "Adana", "آدانا", "Gaziantep", "غازی‌آنتپ",
    "Konya", "قونیه", "Kayseri", "کایسری", "Mersin", "مرسین",
    "Eskişehir", "اسکی‌شهر", "Samsun", "سامسون",
    "Trabzon", "ترابزون", "Bodrum", "بودروم",
    "Marmaris", "مارماریس", "Fethiye", "فتحیه",
    "Alanya", "آلانیا", "Çeşme", "چشمه", "Kuşadası", "کوش‌آداسی",
    "Denizli", "دنیزلی", "Diyarbakır", "دیاربکر",
    "Erzurum", "ارزروم", "Van", "وان", "Rize", "ریزه",
    "Edirne", "ادیرنه", "Çanakkale", "چاناک‌قلعه",
    "Balıkesir", "بالیکسیر", "Tekirdağ", "تکیرداغ",
    "Aydın", "آیدین", "Muğla", "موغلا", "Afyon", "افیون",
    "Sivas", "سیواس", "Malatya", "مالاتیا", "Elazığ", "الازیغ",
    "Şanlıurfa", "شانلی‌اورفا", "Batman", "باتمان",
    "Mardin", "ماردین", "Cappadocia", "کاپادوکیا",
    "Göreme", "گورمه", "Side", "سیده", "Kemer", "کمر",
    "Belek", "بلک", "Didim", "دیدیم", "Ayvalık", "آیوالیک",
    "Assos", "آسوس", "Pamukkale", "پاموک‌قلعه",
    "Ephesus", "افسوس", "Troy", "تروآ",
]
IR_PLACES = [
    "Iran", "ایران", "Gorgan", "گرگان", "Tehran", "تهران",
    "Mashhad", "مشهد", "Isfahan", "اصفهان", "Shiraz", "شیراز",
    "Tabriz", "تبریز", "Karaj", "کرج", "Qom", "قم",
    "Ahvaz", "اهواز", "Kerman", "کرمان", "Yazd", "یزد",
    "Rasht", "رشت", "Ardabil", "اردبیل", "Bandar Abbas", "بندرعباس",
    "Sari", "ساری", "Babol", "بابل", "Amol", "آمل",
    "Kermanshah", "کرمانشاه", "Urmia", "ارومیه",
    "Zahedan", "زاهدان", "Hamedan", "همدان", "Arak", "اراک",
    "Qazvin", "قزوین", "Zanjan", "زنجان", "Sanandaj", "سنندج",
    "Bushehr", "بوشهر", "Kish", "کیش", "Qeshm", "قشم",
]

# Per-server keyword enrichment for obvious concept words the platform
# text omits. FlixTrain sells tickets, but no platform string says
# "ticket" — without this, a ticket question would never match it.
# Keep this map minimal and honest: only words the service truly does.
SERVER_KEYWORDS = {
    "flixtrain-mcp": ["ticket", "fahrkarte", "bilet", "بلیط", "بلیت"],
}

BONBAMA_FA = {
    "bb_search_businesses": "جست‌وجوی کسب‌وکارهای بن‌باما با کلیدواژه و دسته‌بندی",
    "bb_get_business": "جزئیات کامل یک کسب‌وکار: کمپین‌های فعال و کدهای تخفیف",
    "bb_list_categories": "همهٔ دسته‌بندی‌های کسب‌وکار بن‌باما با نام فارسی",
    "bb_search_discounts": "جست‌وجوی کمپین‌های تخفیف فعال در بن‌باما",
    "bb_search_articles": "جست‌وجوی مقالات مجلهٔ بن‌باما",
    "bb_health": "بررسی در دسترس بودن bonbama.ir",
}
BONBAMA_FALLBACK_EN = {
    "bb_search_businesses": "Search Bonbama businesses by keyword and/or category.",
    "bb_get_business": "Full details for one business: name, description, city, address, rating, category, ACTIVE campaigns and literal discount codes.",
    "bb_list_categories": "All Bonbama business categories with Persian names and counts.",
    "bb_search_discounts": "Search ACTIVE discount campaigns site-wide by keyword.",
    "bb_search_articles": "Search the Bonbama magazine (buying guides, discount explainers, best-of-Gorgan lists).",
    "bb_health": "Check whether bonbama.ir is reachable right now.",
}


# ---------------------------------------------------------------- JS parsing
class _JSLexer:
    """Tokeniser for the SERVERS array literal (JS object syntax)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.n = len(text)

    def tokens(self):
        while self.i < self.n:
            c = self.text[self.i]
            if c.isspace():
                self.i += 1
            elif c in "[{]}:,":
                self.i += 1
                yield ("punct", c)
            elif c in "\"'`":
                yield ("str", self._string(c))
            elif c.isdigit() or (c == "-" and self.i + 1 < self.n
                                 and self.text[self.i + 1].isdigit()):
                yield ("num", self._number())
            elif c.isalpha() or c in "_$":
                j = self.i
                while (self.i < self.n and (self.text[self.i].isalnum()
                       or self.text[self.i] in "_$")):
                    self.i += 1
                yield ("ident", self.text[j:self.i])
            else:
                raise ValueError(f"Unexpected char {c!r} at {self.i}")

    def _string(self, quote: str) -> str:
        self.i += 1
        out: list[str] = []
        while self.i < self.n:
            c = self.text[self.i]
            if c == quote:
                self.i += 1
                return "".join(out)
            if c == "\\" and self.i + 1 < self.n:
                e = self.text[self.i + 1]
                simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b",
                          "f": "\f", "v": "\v", "0": "\0",
                          "\\": "\\", "'": "'", '"': '"', "`": "`",
                          "/": "/"}
                if e in simple:
                    out.append(simple[e])
                    self.i += 2
                    continue
                if e == "x":
                    out.append(chr(int(self.text[self.i+2:self.i+4], 16)))
                    self.i += 4
                    continue
                if e == "u":
                    out.append(chr(int(self.text[self.i+2:self.i+6], 16)))
                    self.i += 6
                    continue
                out.append(e)
                self.i += 2
                continue
            out.append(c)
            self.i += 1
        raise ValueError("Unterminated string in SERVERS literal")

    def _number(self):
        j = self.i
        while (self.i < self.n
               and (self.text[self.i].isdigit()
                    or self.text[self.i] in ".eE+-")):
            self.i += 1
        raw = self.text[j:self.i]
        try:
            return int(raw)
        except ValueError:
            return float(raw)


class _JSParser:
    def __init__(self, text: str) -> None:
        self.toks = list(_JSLexer(text).tokens())
        self.i = 0

    def parse(self):
        value = self._value()
        if self.i != len(self.toks):
            raise ValueError("Trailing tokens after SERVERS literal")
        return value

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _next(self):
        tok = self._peek()
        self.i += 1
        return tok

    def _value(self):
        kind, val = self._next()
        if kind == "str":
            return val
        if kind == "num":
            return val
        if kind == "ident":
            if val == "true":
                return True
            if val == "false":
                return False
            if val in ("null", "undefined"):
                return None
            raise ValueError(f"Unexpected identifier {val!r}")
        if kind == "punct" and val == "[":
            out = []
            if self._peek() == ("punct", "]"):
                self._next()
                return out
            while True:
                out.append(self._value())
                kind2, val2 = self._next()
                if kind2 == "punct" and val2 == "]":
                    return out
                if not (kind2 == "punct" and val2 == ","):
                    raise ValueError("Expected , or ] in array")
        if kind == "punct" and val == "{":
            out = {}
            if self._peek() == ("punct", "}"):
                self._next()
                return out
            while True:
                kkind, kval = self._next()
                if kkind not in ("ident", "str"):
                    raise ValueError(f"Bad object key {kval!r}")
                ck, cv = self._next()
                if not (ck == "punct" and cv == ":"):
                    raise ValueError("Expected : after object key")
                out[kval] = self._value()
                kind2, val2 = self._next()
                if kind2 == "punct" and val2 == "}":
                    return out
                if not (kind2 == "punct" and val2 == ","):
                    raise ValueError("Expected , or } in object")
        raise ValueError(f"Unexpected token {kind} {val!r}")


def extract_servers_literal(html: str) -> str:
    """Slice out the `SERVERS = [...]` array literal, string-aware."""
    m = re.search(r"SERVERS\s*=\s*\[", html)
    if not m:
        raise ValueError("SERVERS array not found in platform HTML")
    start = m.end() - 1
    depth = 0
    i = start
    quote = None
    esc = False
    while i < len(html):
        c = html[i]
        if quote is not None:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        i += 1
    raise ValueError("Unbalanced SERVERS array literal")


def parse_platform(html: str) -> list[dict]:
    return _JSParser(extract_servers_literal(html)).parse()


# ---------------------------------------------------------------- sources
def fetch_text(path_or_url: str, live_url: str | None) -> str:
    if live_url:
        req = urllib.request.Request(
            live_url, headers={"User-Agent": "all-mcp-gen-index/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8")
    return Path(path_or_url).read_text(encoding="utf-8")


def platform_server(raw: dict, catalog: str, country: str,
                    places: list[str]) -> dict:
    tools = []
    for t in raw.get("tools", []) or []:
        tools.append({
            "name": t.get("n", ""),
            "desc": t.get("s", ""),
            "desc_fa": t.get("sf", ""),
            "desc_local": t.get("st", ""),
            "params": None,
        })
    return {
        "id": raw.get("id", ""),
        "catalog": catalog,
        "country": country,
        "pypi": raw.get("pypi") or raw.get("id", ""),
        "on_pypi": None,
        "category": raw.get("cat", ""),
        "title": raw.get("title", ""),
        "title_local": raw.get("title_tr") or raw.get("title_de") or "",
        "desc": raw.get("desc", ""),
        "desc_fa": raw.get("desc_fa", ""),
        "desc_local": raw.get("desc_tr") or raw.get("desc_de") or "",
        "repo": raw.get("repo", ""),
        "keywords": list(SERVER_KEYWORDS.get(raw.get("id", ""), [])),
        "place_keywords": list(places),
        "tools": tools,
    }


def bonbama_server() -> dict:
    """bonbama-mcp tools from its server.py docstrings (EN first line)
    + FA one-liners from BONBAMA_FA. Falls back to a static map if the
    source file is missing."""
    tools: dict[str, str] = {}
    try:
        src = Path(BONBAMA_PY).read_text(encoding="utf-8")
        for m in re.finditer(
                r"def (bb_[a-z_0-9]+)\([^)]*\)[^:]*:\n\s+\"\"\"([^\n\"]+)",
                src):
            tools[m.group(1)] = m.group(2).strip()
    except OSError:
        tools = {}
    for name, fallback in BONBAMA_FALLBACK_EN.items():
        tools.setdefault(name, fallback)
    return {
        "id": "bonbama-mcp",
        "catalog": "bonbama",
        "country": "IR",
        "pypi": "bonbama-mcp",
        "on_pypi": None,
        "category": "discounts",
        "title": "Bonbama — Gorgan discounts, businesses & campaigns",
        "title_local": "بن‌باما — تخفیف‌ها و کسب‌وکارهای گرگان",
        "desc": ("Unofficial read-only MCP for bonbama.ir — Gorgan "
                 "discount platform: businesses, live campaigns, "
                 "discount codes, categories, articles."),
        "desc_fa": ("بن‌باما — پلتفرم تخفیف گرگان: کسب‌وکارها، "
                    "کمپین‌های فعال، کدهای تخفیف، دسته‌بندی‌ها و مقالات."),
        "desc_local": "",
        "repo": "https://github.com/mohammad-abbasi-sh/bonbama-mcp",
        "keywords": [],
        "place_keywords": list(IR_PLACES),
        "tools": [
            {"name": name, "desc": desc,
             "desc_fa": BONBAMA_FA.get(name, ""),
             "desc_local": "", "params": None}
            for name, desc in sorted(tools.items())
        ],
    }


def check_pypi(package: str, timeout: float = 5.0) -> bool | None:
    """True if the package exists on PyPI, False if 404, None if the
    check itself failed (network error — caller keeps the old flag)."""
    req = urllib.request.Request(
        f"https://pypi.org/pypi/{package}/json",
        headers={"User-Agent": "all-mcp-gen-index/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return bool(data.get("info", {}).get("version"))
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:
        return None


def load_existing() -> dict | None:
    try:
        return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def merge_previous(new_servers: list[dict], old: dict | None,
                   do_check: bool) -> list[str]:
    """Carry over probed params + on_pypi flags from the previous
    index; run fresh PyPI checks unless disabled. Returns notes."""
    notes: list[str] = []
    old_by_id = {s["id"]: s for s in (old or {}).get("servers", [])}
    for server in new_servers:
        prev = old_by_id.get(server["id"])
        prev_tools = {t["name"]: t for p in [prev] if p
                      for t in p.get("tools", [])}
        for tool in server["tools"]:
            old_tool = prev_tools.get(tool["name"])
            if old_tool and old_tool.get("params") is not None:
                tool["params"] = old_tool["params"]
        if do_check:
            fresh = check_pypi(server["pypi"])
            if fresh is None:
                server["on_pypi"] = prev.get("on_pypi") if prev else None
                notes.append(f"{server['id']}: PyPI check failed, "
                             "kept previous flag")
            else:
                if prev and prev.get("on_pypi") is not None \
                        and prev["on_pypi"] != fresh:
                    notes.append(f"{server['id']}: on_pypi "
                                 f"{prev['on_pypi']} -> {fresh}")
                server["on_pypi"] = fresh
        else:
            server["on_pypi"] = prev.get("on_pypi") if prev else None
    return notes


def probe_servers(servers: list[dict], verbose: bool) -> dict:
    """Enrich tools with real inputSchemas via uvx tools/list.
    Never fatal: failures are recorded per server."""
    from all_mcp import child as child_mod  # noqa: E402

    router = child_mod.Router(spawn_timeout=180.0)
    report: dict = {"probed_at": datetime.now(timezone.utc).isoformat(),
                    "ok": [], "failed": {}, "mismatches": {}}
    try:
        for server in servers:
            pkg = server["pypi"]
            if server.get("on_pypi") is False:
                report["failed"][pkg] = "not on PyPI (skipped)"
                continue
            try:
                live = router.list_tools(pkg, timeout=150.0)
            except child_mod.RouterError as e:
                report["failed"][pkg] = f"{e.reason}: {e}"
                if verbose:
                    print(f"  probe {pkg}: FAILED ({e.reason})")
                continue
            live_by_name = {t.get("name"): t for t in live}
            indexed = {t["name"] for t in server["tools"]}
            live_names = set(live_by_name)
            missing = sorted(indexed - live_names)
            extra = sorted(live_names - indexed)
            if missing or extra:
                report["mismatches"][pkg] = {
                    "in_index_not_live": missing,
                    "live_not_in_index": extra,
                }
            for tool in server["tools"]:
                live_tool = live_by_name.get(tool["name"])
                if live_tool and live_tool.get("inputSchema"):
                    tool["params"] = live_tool["inputSchema"]
            report["ok"].append(pkg)
            if verbose:
                print(f"  probe {pkg}: OK ({len(live)} tools)")
    finally:
        router.close_all()
    return report


def build(live: bool, check_pypi_flag: bool,
          probe: bool, verbose: bool) -> tuple[dict, list[str]]:
    tr_html = fetch_text(TR_HTML, TR_LIVE if live else None)
    de_html = fetch_text(DE_HTML, DE_LIVE if live else None)
    tr_raw = parse_platform(tr_html)
    de_raw = parse_platform(de_html)
    servers = (
        [platform_server(s, "turkey", "TR", TR_PLACES) for s in tr_raw]
        + [platform_server(s, "germany", "DE", DE_PLACES) for s in de_raw]
        + [bonbama_server()]
    )
    old = load_existing()
    notes = merge_previous(servers, old, check_pypi_flag)
    probe_report = None
    if probe:
        probe_report = probe_servers(servers, verbose)
        notes.append(f"probe: {len(probe_report['ok'])} ok, "
                     f"{len(probe_report['failed'])} failed")
    counts: dict = {"servers": len(servers),
                    "tools": sum(len(s["tools"]) for s in servers),
                    "by_catalog": {}}
    for catalog in ("turkey", "germany", "bonbama"):
        subset = [s for s in servers if s["catalog"] == catalog]
        counts["by_catalog"][catalog] = {
            "servers": len(subset),
            "tools": sum(len(s["tools"]) for s in subset),
        }
    payload = {
        "generator": "gen_index.py",
        "sources": {
            "turkey": TR_LIVE if live else TR_HTML,
            "germany": DE_LIVE if live else DE_HTML,
            "bonbama": BONBAMA_PY,
        },
        "counts": counts,
        "servers": servers,
    }
    if probe_report is not None:
        payload["probe"] = probe_report
    elif old and old.get("probe"):
        payload["probe"] = old["probe"]
    return payload, notes


def diff_summary(old: dict | None, new: dict) -> list[str]:
    if old is None:
        return ["new index (no previous file)"]
    old_by_id = {s["id"]: s for s in old.get("servers", [])}
    new_by_id = {s["id"]: s for s in new.get("servers", [])}
    lines: list[str] = []
    for sid in sorted(set(new_by_id) - set(old_by_id)):
        lines.append(f"+ {sid} "
                     f"({len(new_by_id[sid]['tools'])} tools)")
    for sid in sorted(set(old_by_id) - set(new_by_id)):
        lines.append(f"- {sid} (removed)")
    for sid in sorted(set(new_by_id) & set(old_by_id)):
        o, n = old_by_id[sid], new_by_id[sid]
        if len(o["tools"]) != len(n["tools"]):
            lines.append(f"~ {sid}: tools "
                         f"{len(o['tools'])} -> {len(n['tools'])}")
        elif {t["name"] for t in o["tools"]} != \
                {t["name"] for t in n["tools"]}:
            lines.append(f"~ {sid}: tool set changed")
        if o.get("on_pypi") != n.get("on_pypi"):
            lines.append(f"~ {sid}: on_pypi "
                         f"{o.get('on_pypi')} -> {n.get('on_pypi')}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="fetch platform pages from github.io")
    ap.add_argument("--probe", action="store_true",
                    help="enrich with inputSchemas via uvx (slow)")
    ap.add_argument("--no-pypi-check", action="store_true",
                    help="skip the PyPI availability check")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    try:
        payload, notes = build(
            live=args.live,
            check_pypi_flag=not args.no_pypi_check,
            probe=args.probe, verbose=args.verbose)
    except (OSError, ValueError) as e:
        print(f"gen_index: FAILED: {e}", file=sys.stderr)
        return 2
    old = load_existing()
    old_payload = None
    if old:
        old_payload = {k: v for k, v in old.items()
                       if k != "generated_at"}
    counts = payload["counts"]
    by_cat = counts["by_catalog"]
    summary = (
        f"all-mcp index: {counts['servers']} servers, "
        f"{counts['tools']} tools "
        f"(TR {by_cat['turkey']['servers']}/"
        f"{by_cat['turkey']['tools']}, "
        f"DE {by_cat['germany']['servers']}/"
        f"{by_cat['germany']['tools']}, "
        f"bonbama {by_cat['bonbama']['servers']}/"
        f"{by_cat['bonbama']['tools']})"
    )
    print(summary)
    pending = sorted(s["id"] for s in payload["servers"]
                     if s.get("on_pypi") is False)
    print(f"on_pypi: {counts['servers'] - len(pending)} yes, "
          f"{len(pending)} no"
          + (f" ({', '.join(pending)})" if pending else ""))
    for note in notes:
        print(f"  note: {note}")
    changed = diff_summary(old, payload)
    if old_payload == payload:
        print("no changes — index untouched")
        return 0
    for line in changed:
        print(f"  change: {line}")
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1)
                   + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
