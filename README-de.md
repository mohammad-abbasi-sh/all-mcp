# all-mcp — Ein MCP-Server für alle

**Ein MCP-Server, der alle 60 MCP-Server von Mohammad Abbasi enthält — Türkei (32), Deutschland (27), Bonbama (1) — 324 Tools mit einem einzigen Konfigurationseintrag. Fragen Sie alles; er verbindet sich selbst mit dem richtigen MCP.**

> «Was kostet ein Zugticket von Berlin nach München?»
> → findet `flixtrain_search_trains`, ruft es auf und gibt echte Abfahrten und Preise zurück.

## Wie es funktioniert

In diesem Server gibt es keine magische Erkennung und keinen eigenen LLM-Aufruf (dafür bräuchte man einen API-Schlüssel, den Sie nicht haben). Ihre eigene KI erkennt — Claude Desktop, Claude Code, Cursor:

1. **`search_tools(query)`** — Stichwortsuche über alle 324 Tools in vier Sprachen, sortiert nach Themenübereinstimmung, Ortsübereinstimmung (Städte/Länder in der Frage) und Ihrem gespeicherten Standort.
2. **`call_tool(server, tool, arguments)`** — führt das gewählte Tool auf dem richtigen Kind-Server aus (per `uvx` bei Bedarf gestartet) und gibt das echte Ergebnis zurück.

Der Router stellt nur 6 Tools bereit: `search_tools`, `call_tool`, `ask`, `list_servers`, `set_location`, `meta_health`. Eine Suche + ein Aufruf — keine zusätzlichen Roundtrips. `ask(question)` antwortet in einem Schritt: optional mit einem Routing-LLM unter Ihrem eigenen Schlüssel (Gemini / OpenAI / Anthropic / OpenRouter / lokales Ollama), ohne Schlüssel fällt es auf das beste Suchergebnis zurück. Für den Website-Chat gibt es außerdem eine lokale HTTP-Brücke: `uvx all-mcp --http --port 8765` (nur localhost; `/api/ask`, `/api/search`, `/api/call`, `/api/health`).

## Installation

`uvx` erforderlich ([uv installieren](https://docs.astral.sh/uv/)). Zur Claude-Desktop-Konfiguration hinzufügen:

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

`USER_COUNTRY` ist optional (`DE` / `TR` / `IR`): Mehrdeutige Fragen ohne Ortsangabe werden dann zu den Diensten Ihres Landes geleitet. Alternativ das Tool `set_location` verwenden (nur in `~/.config/all-mcp/location.json` auf Ihrem Rechner gespeichert).

## Sortierpriorität

1. **Ausdrückliche Ortsnamen in der Frage gewinnen.** «Bus München nach Hamburg» geht nach Deutschland, auch wenn Ihr gespeicherter Standort die Türkei ist.
2. **Der gespeicherte Standort entscheidet bei Gleichstand.** Ein ortsloses «Busticket-Preis» mit `USER_COUNTRY=DE` zeigt deutsche Optionen zuerst.
3. **Kein Hinweis, kein Standort:** Die Optionen beider Länder werden sortiert zurückgegeben — die KI fragt Sie, statt zu raten.

## Datenschutz

Alles läuft **auf Ihrem eigenen Rechner**: stdio, kein Backend, kein Konto, kein API-Schlüssel, keine Telemetrie. Ihr Standort verlässt den Rechner nie. Schreibaktionen (Warenkorb, Favoriten) erfordern ein explizites `confirm=true`; Zahlungen werden nie automatisiert.

## Noch nicht auf PyPI (6 Server)

`paribu-mcp`, `diyanet-mcp`, `osym-mcp`, `heise-mcp`, `zeit-mcp`, `welt-mcp` — im Index vorhanden, aber die Pakete sind noch nicht veröffentlicht; `call_tool` gibt einen klaren Fehler zurück statt zu hängen.

Lizenz: PolyForm Noncommercial 1.0.0. Mohammad Abbasi — https://almanibama.ir · https://mabbasi.ir
