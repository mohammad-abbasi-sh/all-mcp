"""Optional LLM-powered routing for the `ask` tool (provider-agnostic).

`ask` is an UPGRADE over search_tools + call_tool, not a requirement:
with a routing provider configured, one call routes the question and
executes the tool; without any provider, `ask` falls back to the top
search result (see server.do_ask).

Supported providers (keys ONLY from env vars, never logged, printed,
stored, or included in any error or output — each user uses their OWN
key in their own MCP config):

  gemini     GEMINI_API_KEY      Google AI Studio (free tier)
  openai     OPENAI_API_KEY      gpt-4o-mini class
  anthropic  ANTHROPIC_API_KEY   Haiku class
  openrouter OPENROUTER_API_KEY  free-tier models (good default
                                 for users with no paid key)
  ollama     (no key)            local http://localhost:11434 —
                                 fully free/local, explicit opt-in only

Selection: LLM_PROVIDER env override (one of the names above), else
the first key-based provider with a key set, in the order
gemini -> openai -> anthropic -> openrouter. Ollama is NEVER
auto-selected (it needs no key, so auto-selecting it would break the
no-key search fallback); use LLM_PROVIDER=ollama to opt in.

All providers get the same compact catalog + the same instruction,
and must return JSON: {"server", "tool", "arguments", "reason"}.
Bad JSON, unknown server/tool, or an HTTP error all become a clean
AskError — never a crash, never a blind retry.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

PROVIDERS = ("gemini", "openai", "anthropic", "openrouter", "ollama")

KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

DEFAULT_MODELS = {
    # gemini-3.5-flash: verified live 2026-11-13 (native v1beta).
    "gemini": "gemini-3.5-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    # Verified working free model 2026-11-12 (ai-consult allowlist).
    "openrouter": "nvidia/nemotron-3.5-lightning:free",
    "ollama": "qwen2.5:7b",
}

MODEL_ENV = {
    "gemini": "ALL_MCP_GEMINI_MODEL",
    "openai": "ALL_MCP_OPENAI_MODEL",
    "anthropic": "ALL_MCP_ANTHROPIC_MODEL",
    "openrouter": "ALL_MCP_OPENROUTER_MODEL",
    "ollama": "ALL_MCP_OLLAMA_MODEL",
}

DEFAULT_TIMEOUT = 60.0
OLLAMA_TIMEOUT = 120.0


class AskError(Exception):
    """Routing failed cleanly (config, provider, or bad output)."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise AskError("redirect refused, verify the endpoint")


def model_for(provider: str) -> str:
    return (os.environ.get(MODEL_ENV[provider], "").strip()
            or DEFAULT_MODELS[provider])


def configured_providers() -> list[str]:
    """Key-based providers with a key set, in auto-select order."""
    return [p for p in ("gemini", "openai", "anthropic", "openrouter")
            if os.environ.get(KEY_ENV[p], "").strip()]


def select_provider() -> str | None:
    """Which provider `ask` will use, or None (=> search fallback).

    Raises AskError when LLM_PROVIDER names an unknown provider, or
    names a key-based provider whose key is missing (explicit choice,
    so fail loudly instead of silently falling back).
    """
    override = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if override:
        if override not in PROVIDERS:
            raise AskError(
                f"Unknown LLM_PROVIDER {override!r}; use one of: "
                f"{', '.join(PROVIDERS)}.")
        if override == "ollama":
            return "ollama"
        if not os.environ.get(KEY_ENV[override], "").strip():
            raise AskError(
                f"LLM_PROVIDER={override} but {KEY_ENV[override]} is "
                f"not set. Set the key in your MCP config, or unset "
                f"LLM_PROVIDER to use the search fallback.")
        return override
    configured = configured_providers()
    return configured[0] if configured else None


# ---------------------------------------------------------------- catalog
def build_catalog_text(data: dict) -> str:
    """Compact one-line-per-tool catalog for the routing prompt.

    Format: server [CC] tool(required, params) — one-line description.
    Tools of not-yet-published servers are marked [UNAVAILABLE] and
    the model is told never to choose them.
    """
    lines = [
        "TOOLS (one per line: server [country] tool(required, params)"
        " — description; [UNAVAILABLE] = not on PyPI yet, never choose):"
    ]
    for server in data.get("servers", []):
        unavailable = server.get("on_pypi") is False
        tag = " [UNAVAILABLE]" if unavailable else ""
        for tool in server.get("tools", []):
            params = tool.get("params")
            required = []
            if isinstance(params, dict):
                required = list(params.get("required") or [])
            sig = f"({', '.join(required)})" if required else ""
            lines.append(
                f"{server['id']} [{server.get('country', '?')}] "
                f"{tool['name']}{sig} — {tool.get('desc', '')}{tag}")
    return "\n".join(lines)


INSTRUCTIONS = """\
You route a user's question to exactly ONE tool from the catalog below.
Reply with JSON only, no other text:
{"server": "<server id>", "tool": "<tool name>", "arguments": {...}, "reason": "<one short sentence>"}
Rules:
- Choose the tool whose description best answers the question.
- Prefer tools whose country matches places named in the question (e.g. Munich/Hamburg/Berlin -> German [DE] servers, Istanbul/Ankara -> Turkish [TR] servers, Gorgan -> [IR]).
- If a user location is given and the question names no place, prefer tools from that country.
- Never choose a tool marked [UNAVAILABLE].
- arguments: use the required parameters shown in parentheses; dates as DD.MM.YYYY where the description says so; omit optional parameters unless the question gives them.
- If no tool can answer the question, reply {"server": null, "tool": null, "arguments": {}, "reason": "<why>"}."""


def build_prompt(question: str, catalog_text: str,
                 location: str | None = None) -> tuple[str, str]:
    """(system, user) prompt pair for the routing call.

    location: display string like "Germany (Berlin)" — the user's
    effective location (per-request hint beats saved location); the
    model uses it only when the question names no place.
    """
    user = f"{catalog_text}\n\nQuestion: {question}"
    if location:
        user += (f"\n\nUser location: {location}. Prefer tools from "
                 "this country when the question names no place.")
    return INSTRUCTIONS, user


# ---------------------------------------------------------------- parsing
def extract_routing(text: str) -> dict:
    """Parse the model's routing JSON (tolerant of fences/wrappers)."""
    text = (text or "").strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "server" in parsed \
                and "tool" in parsed:
            return {
                "server": parsed.get("server"),
                "tool": parsed.get("tool"),
                "arguments": (parsed.get("arguments") or {}
                              if isinstance(parsed.get("arguments"),
                                            dict) else {}),
                "reason": str(parsed.get("reason") or ""),
            }
    raise AskError("The routing model returned output that is not "
                   "valid routing JSON.")


def _post_json(url: str, headers: dict, body: dict,
               timeout: float) -> dict:
    """POST JSON, return parsed JSON. No redirects, no retries.
    Errors never include headers (keys live there)."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        raise AskError(
            f"Routing provider returned HTTP {e.code}"
            + (f": {detail}" if detail else "")
            + ". Check the key/model, or unset the provider to use "
            "the search fallback.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AskError(f"Could not reach the routing provider: {e}. "
                       "Check your network, or unset the provider to "
                       "use the search fallback.") from e


def _text_from_gemini(payload: dict) -> str:
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise AskError("Gemini returned an unexpected response shape."
                       ) from e


def _text_from_openai_shape(payload: dict) -> str:
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise AskError("The routing provider returned an unexpected "
                       "response shape.") from e


def _text_from_anthropic(payload: dict) -> str:
    try:
        for block in payload["content"]:
            if block.get("type") == "text":
                return block["text"]
    except (KeyError, TypeError) as e:
        raise AskError("Anthropic returned an unexpected response "
                       "shape.") from e
    raise AskError("Anthropic returned no text content.")


def _text_from_ollama(payload: dict) -> str:
    try:
        return payload["message"]["content"]
    except (KeyError, TypeError) as e:
        raise AskError("Ollama returned an unexpected response shape."
                       ) from e


# ---------------------------------------------------------------- routing
def route_question(question: str, catalog_text: str,
                   provider: str | None = None,
                   location: str | None = None) -> dict:
    """Ask the routing LLM which server/tool answers the question.

    Returns {"server", "tool", "arguments", "reason"} (server/tool
    may be None when nothing fits). Raises AskError on any failure.
    The provider's API key is read from its env var only.
    """
    provider = provider or select_provider()
    if provider is None:
        raise AskError("No routing provider is configured.")
    system, user = build_prompt(question, catalog_text, location)
    model = model_for(provider)
    if provider == "gemini":
        key = os.environ[KEY_ENV["gemini"]]
        payload = _post_json(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent",
            {"x-goog-api-key": key},
            {"systemInstruction": {"parts": [{"text": system}]},
             "contents": [{"parts": [{"text": user}]}],
             "generationConfig": {"responseMimeType": "application/json",
                                  "temperature": 0}},
            DEFAULT_TIMEOUT)
        return extract_routing(_text_from_gemini(payload))
    if provider in ("openai", "openrouter"):
        key = os.environ[KEY_ENV[provider]]
        url = ("https://api.openai.com/v1/chat/completions"
               if provider == "openai"
               else "https://openrouter.ai/api/v1/chat/completions")
        headers = {"Authorization": f"Bearer {key}"}
        if provider == "openrouter":
            headers["X-Title"] = "all-mcp"
        payload = _post_json(url, headers,
                             {"model": model,
                              "messages": [
                                  {"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                              "response_format": {"type": "json_object"},
                              "temperature": 0},
                             DEFAULT_TIMEOUT)
        return extract_routing(_text_from_openai_shape(payload))
    if provider == "anthropic":
        key = os.environ[KEY_ENV["anthropic"]]
        payload = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": 1024, "temperature": 0,
             "system": system,
             "messages": [{"role": "user", "content": user}]},
            DEFAULT_TIMEOUT)
        return extract_routing(_text_from_anthropic(payload))
    if provider == "ollama":
        host = os.environ.get("ALL_MCP_OLLAMA_HOST",
                              "http://localhost:11434").rstrip("/")
        payload = _post_json(
            f"{host}/api/chat", {},
            {"model": model, "stream": False, "format": "json",
             "options": {"temperature": 0},
             "messages": [
                 {"role": "system", "content": system},
                 {"role": "user", "content": user}]},
            OLLAMA_TIMEOUT)
        return extract_routing(_text_from_ollama(payload))
    raise AskError(f"Unknown provider {provider!r}.")
