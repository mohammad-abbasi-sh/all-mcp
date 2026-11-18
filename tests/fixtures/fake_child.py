#!/usr/bin/env python3
"""Minimal fake MCP server (stdio JSON-RPC) for router tests.

Tools: echo_tool (returns its arguments), error_tool (isError),
slow_tool (sleeps 3s, for timeout tests). `--crash` exits(1) at
startup to simulate a child that cannot start.
"""
import json
import sys
import time

TOOLS = [
    {"name": "echo_tool", "description": "Echo arguments back.",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "integer"}},
                     "required": []}},
    {"name": "error_tool", "description": "Always fails.",
     "inputSchema": {"type": "object", "properties": {},
                     "required": []}},
    {"name": "slow_tool", "description": "Sleeps 3s then echoes.",
     "inputSchema": {"type": "object", "properties": {},
                     "required": []}},
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    if "--crash" in sys.argv:
        sys.exit(1)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue  # notification: no reply
        req_id = msg["id"]
        method = msg.get("method", "")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "fake-mcp", "version": "0.1.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": req_id,
                  "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo_tool":
                send({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps({"echo": args})}]}})
            elif name == "error_tool":
                send({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text",
                                 "text": "boom: fake failure"}],
                    "isError": True}})
            elif name == "slow_tool":
                time.sleep(3)
                send({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps({"slow": True})}]}})
            else:
                send({"jsonrpc": "2.0", "id": req_id, "error": {
                    "code": -32602, "message": f"unknown tool {name}"}})
        else:
            send({"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
