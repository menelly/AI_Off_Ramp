"""Tests for the MCP tool list (no network, no running server)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

pytest.importorskip("mcp.types")

from ai_off_ramp.server import TOOL_ANNOTATIONS, _tools

READ_ONLY = {"offramp_get_contacts", "offramp_get_privacy_rules",
             "offramp_get_status", "offramp_get_config_summary"}
SENDS = {"offramp_check_in", "offramp_escalate"}


def test_every_tool_is_annotated():
    tools = _tools()
    assert {t.name for t in tools} == set(TOOL_ANNOTATIONS)
    for t in tools:
        assert t.annotations is not None, t.name


def test_read_only_tools_say_so():
    for t in _tools():
        assert bool(t.annotations.readOnlyHint) == (t.name in READ_ONLY), t.name


def test_sending_tools_are_open_world_not_destructive():
    for t in _tools():
        if t.name in SENDS:
            assert t.annotations.openWorldHint is True
            assert t.annotations.destructiveHint is False
            assert t.annotations.idempotentHint is False
