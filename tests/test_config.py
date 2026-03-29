"""Tests for the configuration loader."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_off_ramp.config import load_config


MINIMAL_CONFIG = """
user:
  name: "TestUser"
  pronouns: "they/them"

contacts:
  - id: "friend"
    name: "Friend"
    relationship: "friend"
    methods:
      email: "friend@test.com"
    preferred_method: "email"
    tiers: ["check_in"]
    visibility: ["user_silent"]

privacy:
  never_share: ["sexuality"]

escalation:
  tiers:
    - level: "check_in"
      delay_minutes: 20
"""


def _write_config(content: str) -> str:
    """Write config to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


class TestConfigLoading:
    def test_loads_minimal_config(self):
        path = _write_config(MINIMAL_CONFIG)
        try:
            config = load_config(path)
            assert config.user.name == "TestUser"
            assert config.user.pronouns == "they/them"
            assert len(config.contacts) == 1
            assert config.contacts[0].id == "friend"
            assert "sexuality" in config.privacy.never_share
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")

    def test_no_contacts_raises(self):
        config_str = """
user:
  name: "TestUser"
contacts: []
"""
        path = _write_config(config_str)
        try:
            with pytest.raises(ValueError, match="at least one contact"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_contact_without_methods_raises(self):
        config_str = """
user:
  name: "TestUser"
contacts:
  - id: "friend"
    name: "Friend"
    methods: {}
    preferred_method: "email"
    tiers: ["check_in"]
    visibility: ["user_silent"]
"""
        path = _write_config(config_str)
        try:
            with pytest.raises(ValueError, match="no contact methods"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_env_var_resolution(self):
        os.environ["TEST_OFFRAMP_TOKEN"] = "secret123"
        config_str = """
user:
  name: "TestUser"
contacts:
  - id: "friend"
    name: "Friend"
    methods:
      email: "friend@test.com"
    preferred_method: "email"
    tiers: ["check_in"]
    visibility: ["user_silent"]
integrations:
  telegram:
    bot_token: "env:TEST_OFFRAMP_TOKEN"
"""
        path = _write_config(config_str)
        try:
            config = load_config(path)
            assert config.integrations.telegram is not None
            assert config.integrations.telegram.bot_token == "secret123"
        finally:
            os.unlink(path)
            del os.environ["TEST_OFFRAMP_TOKEN"]

    def test_missing_env_var_raises(self):
        config_str = """
user:
  name: "TestUser"
contacts:
  - id: "friend"
    name: "Friend"
    methods:
      email: "friend@test.com"
    preferred_method: "email"
    tiers: ["check_in"]
    visibility: ["user_silent"]
integrations:
  telegram:
    bot_token: "env:NONEXISTENT_VAR_12345"
"""
        path = _write_config(config_str)
        try:
            with pytest.raises(ValueError, match="not set"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_defaults_applied(self):
        path = _write_config(MINIMAL_CONFIG)
        try:
            config = load_config(path)
            assert config.user.timezone == "UTC"
            assert config.escalation.active_session_only is True
            assert config.audit.log_messages is True
        finally:
            os.unlink(path)
