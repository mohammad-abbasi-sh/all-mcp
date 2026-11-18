"""Local HTTP bridge for the website chat UI (no backend of ours).

The user runs the meta-server on their own machine and opens our
site; the site's chat talks to http://localhost:8765.

  uvx all-mcp --http --port 8765

Endpoints (all JSON):
  GET  /api/health   router status (servers indexed, LLM configured
                     yes/no — NEVER any key material)
  POST /api/search   {"query", "limit"?} -> ranked tools
  POST /api/call     {"server", "tool", "arguments"?, "timeout"?}
  POST /api/ask      {"question", "location"?} -> one-step answer
                     (LLM routing if a provider is configured, else
                     top search result auto-executed + alternatives
                     listed); the optional location hint
                     {"country", "city?"} comes from the chat UI's
                     browser memory and beats the saved location.
                     The response includes `steps` (the routing
                     trace the UI animates) and `sources` (citation
                     links) plus a compact one-line-per-step `trace`.
  GET  /             tiny info page

Security:
- Binds 127.0.0.1 ONLY. Nothing is reachable from the network.
- CORS allows only our GitHub Pages origin
  (https://mohammad-abbasi-sh.github.io) and localhost/127.0.0.1
  (any port, for local development). Other origins get no CORS
  headers, so browsers block them.
- Request bodies are capped at 1 MB. No key is ever accepted from
  a request — keys come from the server's own environment only.
- Stdlib only (no new dependency): bonbama-mcp uses Starlette for
  its hosted MCP endpoint, but this bridge needs just four REST
  routes, so http.server is enough.

The chat UI itself lives on the site; this module only serves it.
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import server as _srv

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 1_000_000

_ALLOWED_ORIGIN = re.compile(
    r"^https://mohammad-abbasi-sh\.github\.io$"
    r"|^https?://localhost(:\d+)?$"
    r"|^https?://127\.0\.0\.1(:\d+)?$")

INFO_HTML = """\
<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>all-mcp local bridge</title></head><body>
<h1>all-mcp local bridge</h1>
<p>The meta-MCP router is running on this machine (localhost only).</p>
<ul>
<li><code>GET /api/health</code> — status</li>
<li><code>POST /api/search</code> — {"query", "limit"?}</li>
<li><code>POST /api/call</code> — {"server", "tool", "arguments"?, "timeout"?}</li>
<li><code>POST /api/ask</code> — {"question"}</li>
</ul>
<p>Open the chat on the Turkey/Germany MCP site to use it.</p>
</body></html>
"""


def _cors_headers(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    origin = handler.headers.get("Origin", "")
    headers: dict[str, str] = {}
    if origin and _ALLOWED_ORIGIN.match(origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type"
        headers["Access-Control-Max-Age"] = "86400"
        headers["Vary"] = "Origin"
    return headers


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------
    def log_message(self, fmt, *args):  # keep stderr quiet-ish
        pass

    def _send(self, status: int, payload, content_type="application/json"):
        body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if content_type == "application/json"
                else payload.encode("utf-8"))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in _cors_headers(self).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, payload)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large (max 1 MB)")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"invalid JSON body: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    # -- routes --------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        for k, v in _cors_headers(self).items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json(200, _srv.do_health())
        elif path in ("/", "/index.html"):
            self._send(200, INFO_HTML, content_type="text/html")
        else:
            self._send_json(404, {"error": f"unknown route {path}"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        try:
            if path == "/api/search":
                query = body.get("query", "")
                limit = body.get("limit", 8)
                if not isinstance(query, str) or not query.strip():
                    self._send_json(400, {"error": "query is required"})
                    return
                self._send_json(200, _srv.do_search(query, limit=limit))
            elif path == "/api/call":
                server = body.get("server", "")
                tool = body.get("tool", "")
                if not server or not tool:
                    self._send_json(
                        400, {"error": "server and tool are required"})
                    return
                self._send_json(200, _srv.do_call(
                    server, tool,
                    arguments=body.get("arguments"),
                    timeout=body.get("timeout", 90)))
            elif path == "/api/ask":
                question = body.get("question", "")
                if not isinstance(question, str) or not question.strip():
                    self._send_json(400,
                                    {"error": "question is required"})
                    return
                self._send_json(200, _srv.do_ask(
                    question, location=body.get("location")))
            else:
                self._send_json(404, {"error": f"unknown route {path}"})
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:  # never crash the bridge on one request
            self._send_json(500, {"error": f"internal error: {e}"})


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Localhost-only HTTP server (port 0 = ephemeral, for tests)."""
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port: int = DEFAULT_PORT) -> None:
    httpd = make_server(port)
    print(f"all-mcp HTTP bridge on http://127.0.0.1:{port} "
          "(localhost only — press Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
