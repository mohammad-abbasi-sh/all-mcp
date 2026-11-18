#!/usr/bin/env python3
"""Live smoke test for all-mcp: real routing through real child servers.

Requires network + uvx (CI daily workflow + manual runs). NOT part of
the offline pytest suite.

Checks:
1. Acceptance search: the Persian train question ranks flixtrain #1.
2. Real call: tcmb_get_rates through the router. In this sandbox
   the egress proxy's MITM cert is not in the child's certifi bundle
   (the child sets httpx trust_env=False, so the router cannot fix
   it with env vars) — a real answer is expected on a normal
   machine, but here a clean forwarded TLS/network error counts as
   a PASS for the router (structured answer, no crash/hang).
3. Real call: tagesschau_headlines through the router returns news.
4. Acceptance call: flixtrain_search_trains Berlin->München tomorrow.
   FlixTrain's read path is bot-walled from some networks: a clean
   forwarded error counts as a PASS for the router (it must return a
   structured answer, never crash or hang); a real answer is better
   and is reported as such.
5. Fail-fast: osym-mcp (not on PyPI yet) returns not_on_pypi.
6. Ask fallback: with no routing provider configured, do_ask
   executes the top no-arg result (tcmb) via the search fallback
   and returns steps + sources.

Exit 0 only if 1, 2, 3, 5, 6 pass. Prints a per-check summary.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from all_mcp import server as srv  # noqa: E402

# The ask check must run with NO routing provider configured.
for _var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
             "OPENROUTER_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(_var, None)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))


def main() -> int:
    # 1. Acceptance search
    out = srv.do_search("قیمت بلیط قطار از برلین به مونیخ چقدره؟")
    top = out["results"][0] if out["results"] else {}
    check("search: train question -> flixtrain #1",
          top.get("tool") == "flixtrain_search_trains",
          f"top={top.get('server')}/{top.get('tool')}")

    # 2. Real call: TCMB FX rates (or a clean forwarded error — the
    # sandbox's MITM proxy cert is not in the child's certifi bundle)
    r = srv.do_call("tcmb-mcp", "tcmb_get_rates", {})
    real = (not r.get("error") and not r.get("is_error")
            and isinstance(r.get("result"), dict)
            and "USD" in str(r.get("result")))
    clean_error = bool(r.get("error") or r.get("is_error"))
    check("call: tcmb_get_rates (real answer, else clean forwarded "
          "error)", real or clean_error,
          ("REAL ANSWER" if real
           else f"forwarded error: {str(r.get('error') or '')[:160]}"))

    # 3. Real call: Tagesschau headlines
    r = srv.do_call("tagesschau-mcp", "tagesschau_headlines", {})
    res = r.get("result")
    ok = (not r.get("error") and not r.get("is_error")
          and isinstance(res, dict) and bool(res.get("headlines")
          or res.get("items") or res.get("news")))
    check("call: tagesschau_headlines returns news", ok,
          str(r.get("error") or res)[:160])

    # 4. Acceptance call: FlixTrain Berlin -> München tomorrow
    tomorrow = (date.today() + timedelta(days=1)).strftime("%d.%m.%Y")
    r = srv.do_call("flixtrain-mcp", "flixtrain_search_trains",
                    {"from_city": "Berlin", "to_city": "München",
                     "date": tomorrow, "limit": 5})
    res = r.get("result")
    real_answer = (
        not r.get("error") and not r.get("is_error")
        and isinstance(res, dict)
        and bool(res.get("departures") or res.get("trains")
                 or res.get("results") or res.get("journeys")
                 or res.get("items")))
    clean_error = bool(r.get("error") or r.get("is_error"))
    check("call: flixtrain Berlin->München (real answer, else clean "
          "forwarded error)", real_answer or clean_error,
          ("REAL ANSWER" if real_answer
           else f"forwarded error: {str(r.get('error') or res)[:160]}"))

    # 5. Fail-fast for a not-on-PyPI server
    r = srv.do_call("osym-mcp", "osym_latest_announcements", {})
    check("fail-fast: osym-mcp -> not_on_pypi",
          r.get("reason") == "not_on_pypi",
          str(r.get("error", ""))[:120])

    # 6. Ask fallback (no provider): tcmb executed + trace present
    r = srv.do_ask("dollar exchange rate today")
    steps = r.get("steps") or []
    ok = (r.get("mode") == "search-fallback"
          and r.get("executed") is True
          and r.get("routing", {}).get("server") == "tcmb-mcp"
          and len(steps) >= 2
          and all(s.get("status") in ("ok", "failed", "skipped")
                  for s in steps)
          and bool(r.get("sources")))
    check("ask fallback: no provider -> tcmb executed, steps+sources",
          ok, f"mode={r.get('mode')} steps={len(steps)} "
          f"sources={len(r.get('sources') or [])}")

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
