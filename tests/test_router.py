"""Offline tests for the child-server router (fake child fixture).

Covers: successful calls, tool-level errors forwarded (not raised),
child crash isolation, timeouts + session eviction, missing uvx.

Run with:  python -m pytest tests/test_router.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from all_mcp import child as C  # noqa: E402

FIXTURE = str(Path(__file__).resolve().parent / "fixtures"
              / "fake_child.py")
FAKE_CMD = [sys.executable, FIXTURE]
CRASH_CMD = [sys.executable, FIXTURE, "--crash"]


@pytest.fixture(autouse=True)
def _overrides():
    C._COMMAND_OVERRIDES["fake-mcp"] = FAKE_CMD
    C._COMMAND_OVERRIDES["crash-mcp"] = CRASH_CMD
    yield
    C._COMMAND_OVERRIDES.clear()


def test_call_echo():
    router = C.Router()
    try:
        out = router.call("fake-mcp", "echo_tool", {"x": 41})
        assert out["is_error"] is False
        assert '"x": 41' in out["content"][0]["text"]
    finally:
        router.close_all()


def test_session_cached_and_reused():
    router = C.Router()
    try:
        router.call("fake-mcp", "echo_tool", {"x": 1})
        router.call("fake-mcp", "echo_tool", {"x": 2})
        assert router.cached_servers() == ["fake-mcp"]
    finally:
        router.close_all()


def test_tool_error_returned_not_raised():
    router = C.Router()
    try:
        out = router.call("fake-mcp", "error_tool", {})
        assert out["is_error"] is True
        assert "boom" in out["content"][0]["text"]
    finally:
        router.close_all()


def test_unknown_tool_raises_router_error():
    router = C.Router()
    try:
        with pytest.raises(C.RouterError) as e:
            router.call("fake-mcp", "no_such_tool", {})
        assert e.value.reason == "child_error"
    finally:
        router.close_all()


def test_list_tools():
    router = C.Router()
    try:
        tools = router.list_tools("fake-mcp")
        names = {t["name"] for t in tools}
        assert names == {"echo_tool", "error_tool", "slow_tool"}
        echo = [t for t in tools if t["name"] == "echo_tool"][0]
        assert echo["inputSchema"]["properties"]["x"]["type"] == "integer"
    finally:
        router.close_all()


def test_crashing_child_never_kills_router():
    router = C.Router()
    try:
        with pytest.raises(C.RouterError) as e:
            router.call("crash-mcp", "echo_tool", {})
        assert e.value.reason == "spawn_failed"
        # The router itself is fine: a healthy child still answers.
        out = router.call("fake-mcp", "echo_tool", {"x": 1})
        assert out["is_error"] is False
        assert router.cached_servers() == ["fake-mcp"]
    finally:
        router.close_all()


def test_timeout_evicts_session_then_recovers():
    router = C.Router()
    try:
        with pytest.raises(C.RouterError) as e:
            router.call("fake-mcp", "slow_tool", {}, timeout=1)
        assert e.value.reason == "timeout"
        assert router.cached_servers() == []
        out = router.call("fake-mcp", "echo_tool", {"x": 1})
        assert out["is_error"] is False
    finally:
        router.close_all()


def test_missing_uvx_gives_clear_error(monkeypatch):
    monkeypatch.delenv("ALL_MCP_UVX", raising=False)
    monkeypatch.setattr(C.shutil, "which", lambda *_a, **_k: None)
    router = C.Router()
    try:
        with pytest.raises(C.RouterError) as e:
            router.call("tcmb-mcp", "tcmb_get_rates", {})
        assert e.value.reason == "uvx_missing"
        assert "uvx" in str(e.value)
    finally:
        router.close_all()
