"""User location for location-first routing.

Two mechanisms, both strictly local — location NEVER leaves the
user's machine (no network call, no telemetry, no transmission):

1. `USER_COUNTRY` env var in the MCP config (DE / TR / IR), set once
   at install. This is the primary mechanism and takes precedence.
2. `set_location` tool (fallback when the env var is not set):
   persists to a local file only —
   `$XDG_CONFIG_HOME/all-mcp/location.json` (default
   `~/.config/all-mcp/location.json`).

Ranking priority (implemented in index.SearchIndex.search):
explicit place names in the query > saved location > no default.
With no cue and no location, both countries' options are returned
ranked and the client LLM asks the user.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ENV_VAR = "USER_COUNTRY"
VALID_COUNTRIES = {"DE", "TR", "IR"}

COUNTRY_NAMES = {
    "DE": "Germany",
    "TR": "Türkiye",
    "IR": "Iran",
}


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "all-mcp"


def location_file() -> Path:
    return config_dir() / "location.json"


def read_saved_location() -> dict | None:
    """The location from the local file, or None if never set/invalid."""
    try:
        data = json.loads(location_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    country = str(data.get("country", "")).upper()
    if country not in VALID_COUNTRIES:
        return None
    return {
        "country": country,
        "city": data.get("city") or None,
        "source": "file",
    }


def resolve_location() -> dict | None:
    """Effective location: USER_COUNTRY env var wins over the file."""
    env = os.environ.get(ENV_VAR, "").strip().upper()
    if env in VALID_COUNTRIES:
        return {"country": env, "city": None, "source": "env"}
    return read_saved_location()


def save_location(country: str, city: str | None = None) -> dict:
    """Persist a location to the local file. Returns what was saved
    plus the effective location (the env var still wins if set)."""
    country = (country or "").strip().upper()
    if country not in VALID_COUNTRIES:
        raise ValueError(
            f"country must be one of {sorted(VALID_COUNTRIES)} "
            f"(DE = Germany, TR = Türkiye, IR = Iran); got {country!r}"
        )
    city = (city or "").strip() or None
    path = location_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"country": country, "city": city,
         "set_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    effective = resolve_location()
    out = {"saved": {"country": country, "city": city},
           "effective": effective}
    if effective and effective.get("source") == "env":
        out["note"] = (
            f"Saved, but {ENV_VAR}={effective['country']} in your MCP "
            f"config takes precedence. Remove that env var to use the "
            f"saved location."
        )
    return out
