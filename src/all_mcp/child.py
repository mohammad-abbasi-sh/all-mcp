"""Minimal MCP stdio client: spawns child servers via `uvx` and talks
JSON-RPC to them over their stdin/stdout pipes.

Design notes:
- One `ChildSession` per child package, cached in `Router` so repeat
  calls don't re-spawn (uvx install on first use is the slow part).
- A reader thread per child pushes parsed messages into a queue; calls
  wait on the queue with a deadline. A broken child is killed, evicted
  from the cache, and NEVER takes the router or other children down.
- The child inherits the router's environment, so the user's own
  session variables (HEPSIBURADA_SESSION, MIGROS_SESSION, ...) flow
  through untouched. Nothing is ever logged, printed, or persisted
  from the environment.
- No MCP SDK session objects are cached: the client is plain
  subprocess + JSON-RPC, so there is no async lifecycle to keep alive
  across tool calls.
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import subprocess
import threading

PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "all-mcp"
CLIENT_VERSION = "0.1.0"

# Test-only: map a package name to an explicit command instead of
# `uvx <pkg>`. Used by tests/fixtures/fake_child.py. Never set by the
# server itself.
_COMMAND_OVERRIDES: dict[str, list[str]] = {}


class RouterError(Exception):
    """The router could not reach or use a child server."""

    def __init__(self, message: str, reason: str = "error") -> None:
        super().__init__(message)
        self.reason = reason


class ChildSession:
    """One running child MCP server process + its JSON-RPC channel."""

    def __init__(self, package: str, timeout: float = 120.0) -> None:
        self.package = package
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._inbox: queue.Queue[dict] = queue.Queue()
        self._stderr_tail: list[str] = []
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_drain: threading.Thread | None = None
        self._lock = threading.Lock()
        self._start()

    # -- lifecycle ---------------------------------------------------
    def _command(self) -> list[str]:
        if self.package in _COMMAND_OVERRIDES:
            return list(_COMMAND_OVERRIDES[self.package])
        uvx = os.environ.get("ALL_MCP_UVX") or shutil.which("uvx")
        if not uvx:
            raise RouterError(
                "uvx is not installed, so child MCP servers cannot be "
                "started. Install uv (https://docs.astral.sh/uv/) and "
                "make sure `uvx` is on your PATH, then try again.",
                reason="uvx_missing",
            )
        return [uvx, "--quiet", self.package]

    def _start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            raise RouterError(
                f"Could not start `{self.package}`: {e}. Is uvx on PATH?",
                reason="spawn_failed",
            ) from e
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._reader = threading.Thread(
            target=self._read_loop, args=(self._proc.stdout,), daemon=True
        )
        self._reader.start()
        self._stderr_drain = threading.Thread(
            target=self._drain_stderr, args=(self._proc.stderr,), daemon=True
        )
        self._stderr_drain.start()
        self._initialize()

    def _read_loop(self, stream) -> None:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate stray non-JSON stdout lines
            if isinstance(msg, dict):
                self._inbox.put(msg)

    def _drain_stderr(self, stream) -> None:
        for line in stream:
            self._stderr_tail.append(line.rstrip())
            del self._stderr_tail[:-20]  # keep the last 20 lines only

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- JSON-RPC ----------------------------------------------------
    def _send(self, payload: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def _await(self, req_id: int, timeout: float) -> dict:
        try:
            while True:
                msg = self._inbox.get(timeout=timeout)
                if msg.get("id") == req_id:
                    return msg
                # Notifications and other ids are ignored: only one
                # request is outstanding per session at a time.
        except queue.Empty as e:
            raise RouterError(
                f"`{self.package}` did not answer within {timeout:g}s.",
                reason="timeout",
            ) from e

    def _request(self, method: str, params: dict | None,
                 timeout: float) -> dict:
        with self._lock:
            if not self.alive:
                raise RouterError(
                    f"`{self.package}` exited before the request.",
                    reason="child_exited",
                )
            req_id = next(self._ids)
            payload: dict = {
                "jsonrpc": "2.0", "id": req_id, "method": method,
            }
            if params is not None:
                payload["params"] = params
            try:
                self._send(payload)
            except (BrokenPipeError, OSError) as e:
                raise RouterError(
                    f"Could not write to `{self.package}`: {e}",
                    reason="child_exited",
                ) from e
            resp = self._await(req_id, timeout)
        if "error" in resp:
            err = resp["error"]
            raise RouterError(
                f"`{self.package}` returned an error: "
                f"{err.get('message', err)}",
                reason="child_error",
            )
        return resp.get("result", {})

    def _initialize(self) -> None:
        try:
            self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME,
                               "version": CLIENT_VERSION},
            }, timeout=self._timeout)
        except RouterError as e:
            if e.reason in ("child_exited", "timeout") or not self.alive:
                detail = " ".join(self._stderr_tail[-3:]).strip()
                hint = ""
                if detail:
                    hint = f" Child said: {detail[:300]}"
                code = self._proc.poll() if self._proc else None
                raise RouterError(
                    f"Could not start `{self.package}` "
                    f"(exit code {code}). The package may not be "
                    f"published on PyPI yet, or uvx could not fetch "
                    f"it — check your network and that the package "
                    f"name is right.{hint}",
                    reason="spawn_failed",
                ) from e
            raise
        # initialized notification (no id, no response expected)
        try:
            self._send({"jsonrpc": "2.0",
                        "method": "notifications/initialized"})
        except (BrokenPipeError, OSError):
            pass

    # -- MCP operations ----------------------------------------------
    def list_tools(self, timeout: float = 60.0) -> list[dict]:
        result = self._request("tools/list", None, timeout=timeout)
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict | None,
                  timeout: float = 90.0) -> dict:
        """Call one tool. Returns the raw result dict (content/isError)."""
        return self._request("tools/call", {
            "name": name, "arguments": arguments or {},
        }, timeout=timeout)


class Router:
    """Spawns child servers on demand, caches live sessions, and routes
    tool calls. One broken child never affects the others."""

    def __init__(self, spawn_timeout: float = 120.0) -> None:
        self._sessions: dict[str, ChildSession] = {}
        self._lock = threading.Lock()
        self._spawn_timeout = spawn_timeout

    def _get_session(self, package: str) -> ChildSession:
        with self._lock:
            sess = self._sessions.get(package)
            if sess is not None and sess.alive:
                return sess
            if sess is not None:
                del self._sessions[package]
        sess = ChildSession(package, timeout=self._spawn_timeout)
        with self._lock:
            self._sessions[package] = sess
        return sess

    def _evict(self, package: str) -> None:
        with self._lock:
            sess = self._sessions.pop(package, None)
        if sess is not None:
            sess.close()

    def call(self, package: str, tool: str, arguments: dict | None,
             timeout: float = 90.0) -> dict:
        """Call `tool` on the child server for `package`.

        Returns {"content": [...], "is_error": bool}. Raises RouterError
        only when the child cannot be reached at all; a tool-level
        failure (isError) is returned, not raised, so the calling AI
        sees the child server's own error text.
        """
        try:
            sess = self._get_session(package)
            result = sess.call_tool(tool, arguments, timeout=timeout)
        except RouterError:
            self._evict(package)
            raise
        return {
            "content": result.get("content", []),
            "is_error": bool(result.get("isError", False)),
        }

    def list_tools(self, package: str, timeout: float = 60.0) -> list[dict]:
        try:
            sess = self._get_session(package)
            return sess.list_tools(timeout=timeout)
        except RouterError:
            self._evict(package)
            raise

    def cached_servers(self) -> list[str]:
        with self._lock:
            return sorted(p for p, s in self._sessions.items() if s.alive)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            sess.close()
