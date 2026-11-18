# all-mcp — One MCP Server for All of Them

**One MCP server containing all 60 of Mohammad Abbasi's MCP servers — Turkey (32), Germany (27), Bonbama (1) — 324 tools behind a single config entry. Ask anything; it connects to the right MCP itself.**

Ask in Persian, Turkish, German, or English:

> «قیمت بلیط قطار از برلین به مونیخ چقدره؟»
> → finds `flixtrain_search_trains`, calls it, returns real departures and prices.

> «قیمت اتوبوس مونیخ به هامبورگ»
> → routes to **Germany** transport, not Turkey — because the question names German cities.

## How it actually works (honest version)

There is no magic detection inside this server. By default, your own AI — Claude Desktop, Claude Code, Cursor — does the detecting:

1. **`search_tools(query)`** — keyword search over all 324 tools in all four languages, ranked by topic match, place match (cities/countries named in the question), and your saved location. Returns the best tools with server, country, and a one-line description.
2. **`call_tool(server, tool, arguments)`** — runs the chosen tool on the right child server (spawned on demand via `uvx`) and returns its real result.

Exposing all 324 tools at once would drown any client (context blowup, wrong picks). So the router exposes only 6 tools — the standard router pattern:

| Tool | What it does |
|---|---|
| `search_tools(query, limit?)` | Ranked tools for a question (any language) |
| `call_tool(server, tool, arguments?, timeout?)` | Run one tool on one child server |
| `ask(question, location?)` | One-step answer: route + run + explain (see below) |
| `list_servers(catalog?, country?, category?)` | Browse the 60 servers |
| `set_location(country, city?)` | Save your country for location-first routing (local file only) |
| `meta_health()` | Index counts, PyPI status, uvx, current location, routing provider |

One search + one call — no extra round trips. Or one `ask` — see next section.

## `ask`: one-step answers (optional routing LLM)

`ask(question)` answers in a single call: it routes the question to the right server+tool+arguments, executes it, and returns the result plus a one-line routing explanation, alternatives, a routing trace (`steps`: one entry per routing/execution attempt, `{server, tool, status, ms}`), and citation links (`sources`).

Routing needs a small LLM. It uses **your own key** (or a local model) — the router never ships a key, and the key never leaves your machine except to the provider you chose:

| Provider | Env var | Default model | Free? |
|---|---|---|---|
| Gemini | `GEMINI_API_KEY` | `gemini-3.5-flash` | Free tier at [AI Studio](https://aistudio.google.com) |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | Paid |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` | Paid |
| OpenRouter | `OPENROUTER_API_KEY` | `nvidia/nemotron-3.5-lightning:free` | Free-tier models — good default with no paid key |
| Ollama (local) | — | `qwen2.5:7b` | Fully free/local, no key at all |

Selection: `LLM_PROVIDER` env override (`gemini` / `openai` / `anthropic` / `openrouter` / `ollama`), else the first configured provider in that order. Ollama is only used when explicitly selected (`LLM_PROVIDER=ollama`, host `http://localhost:11434` by default; `ollama pull qwen2.5:7b` first). Model overrides: `ALL_MCP_<PROVIDER>_MODEL` (e.g. `ALL_MCP_GEMINI_MODEL`).

**No provider at all?** `ask` still works: it falls back to the top search result — auto-executed when it needs no arguments, otherwise returned with the required parameters listed. `search_tools` + `call_tool` always work with no key at all.

## HTTP bridge: the website chat UI

The site's chat talks to the router running on your own machine (local bridge — still no backend of ours):

```bash
uvx all-mcp --http --port 8765
```

Localhost-only (`127.0.0.1`). Endpoints:

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/health` | — | servers/tools indexed, LLM configured yes/no (never the key) |
| `POST /api/search` | `{"query", "limit"?}` | ranked tools |
| `POST /api/call` | `{"server", "tool", "arguments"?}` | the tool result |
| `POST /api/ask` | `{"question", "location"?}` | one-step answer + `steps` + `sources` |

```bash
curl -X POST http://127.0.0.1:8765/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "قیمت بلیط قطار از برلین به مونیخ چقدره؟"}'
```

CORS allows only `https://mohammad-abbasi-sh.github.io` and `localhost` origins. `/api/ask` accepts an optional per-request `location` hint (`{"country": "DE", "city": "Berlin"}`) sent by the chat UI from its browser memory — it beats the saved location, and explicit place names in the question still beat it. The response's `steps` array is the live routing trace the chat UI animates ("connecting to flixtrain-mcp…"), and `sources` are the citation links.

## Install (one config entry)

Requires `uvx` ([install uv](https://docs.astral.sh/uv/)). Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "all-mcp": {
      "command": "uvx",
      "args": ["all-mcp"],
      "env": {
        "USER_COUNTRY": "DE"
      }
    }
  }
}
```

`USER_COUNTRY` is optional (`DE` / `TR` / `IR`): it tells the router where you are, so an ambiguous question with no place names routes to your country's services. Set it once at install. Without it, use the `set_location` tool (saved to `~/.config/all-mcp/location.json` on your machine only).

Then just ask questions. Try: «هوای برلین چطوره؟», «dollar exchange rate today», «Fußball Ergebnisse Bundesliga», «تخفیف گرگان».

## Ranking priority

1. **Explicit place names in the question win.** «اتوبوس مونیخ به هامبورگ» routes to Germany even if your saved location is Turkey.
2. **A per-request location hint beats the saved location.** The chat UI sends `{"country", "city?"}` from its browser memory with each `/api/ask` (or the `ask` tool's `location` argument) — it wins over `USER_COUNTRY`/`set_location`, but still loses to place names in the question.
3. **Saved location breaks ties.** A bare «قیمت بلیط اتوبوس» with `USER_COUNTRY=DE` ranks German options first; with `TR`, Turkish ones.
4. **No cue, no location:** both countries' options are returned ranked — your AI asks you which country you mean instead of guessing.

Every search result carries the server's **country + one-line description**, so the AI can itself disambiguate.

## Privacy

Everything runs **locally on your machine**: stdio, no backend, no account, no telemetry. No API key is needed for search/call. Your location (env var, local file, or per-request hint) never leaves the machine. Child servers inherit your own session variables (see below) and nothing is ever logged, printed, or stored. The only network call the router itself makes is `ask`'s routing call — and only when you configured a provider: your question goes to that provider (or stays on your machine with Ollama), and your key is sent only in the request header to that provider, never logged or stored.

## Account actions (sessions)

Two servers have write tools that act inside your own account (cart, favorites): `hepsiburada-mcp` and `migros-mcp`. They use **your own session cookie**, passed as an env var in your MCP config — it never leaves your machine:

```json
"env": {
  "USER_COUNTRY": "DE",
  "HEPSIBURADA_SESSION": "your-session-cookie",
  "MIGROS_SESSION": "your-session-cookie"
}
```

Write actions always need explicit `confirm=true` in the arguments — enforced by the child servers, forwarded unchanged by the router. Payment is never automated: order tools return a checkout link and stop.

## Servers not on PyPI yet (6)

These servers are in the index but their packages are not published yet, so `call_tool` returns a clear error instead of hanging: `paribu-mcp`, `diyanet-mcp`, `osym-mcp`, `heise-mcp`, `zeit-mcp`, `welt-mcp`. They will work automatically once published (the index regenerates on every factory run).

## The hierarchy

1. **all-mcp (this repo)** — the main reference: one entry point for everything.
2. **Turkey MCP + Germany MCP** — the catalogs and platforms ([turkey-mcp](https://mohammad-abbasi-sh.github.io/turkey-mcp/), [germany-mcp](https://mohammad-abbasi-sh.github.io/germany-mcp/)).
3. **Individual MCP repos** — one repo per service.

Every new server added by the daily factory runs updates this index too. The index is generated, never hand-written:

```bash
python3 gen_index.py            # regenerate from the local catalogs
python3 gen_index.py --probe    # also refresh real inputSchemas via uvx
```

`gen_index.py` is cron-friendly: non-interactive, idempotent (exit 0, file untouched when nothing changed), atomic writes, and a short summary on stdout. Factory cron command:

```bash
python3 ~/workspace/github-profile/projects/all-mcp/gen_index.py
```

## Development

```bash
python -m pytest tests/ -q        # offline suite (103 tests)
python tests/smoke_live.py        # live smoke: 6 checks (needs network + uvx)
```

## Cite this project

If you use all-mcp, build on it, or write about it, please credit the source:

> Abbasi, Mohammad. *all-mcp — One MCP Server for All of Them*. v0.1.0, 2026. https://github.com/mohammad-abbasi-sh/all-mcp

A ready-to-copy citation is also available from GitHub's **Cite this repository** button (see `CITATION.cff`). The router design and the tool index are Mohammad Abbasi's work — commercial use needs a separate license (see below).

## License

PolyForm Noncommercial 1.0.0 — free for personal and non-commercial use. Commercial use needs a separate license: contact [Mohammad Abbasi](https://github.com/mohammad-abbasi-sh).

Built by Mohammad Abbasi — https://almanibama.ir · https://mabbasi.ir
