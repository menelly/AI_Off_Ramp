"""Tests for the ntfy push channel.

Uses a tiny real HTTP server on 127.0.0.1 standing in for ntfy, so these
exercise the actual request (URL, JSON body, auth header), not a mock of it.
"""

import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from aiohttp import web

from ai_off_ramp import contacts as C
from ai_off_ramp.audit import AuditLog
from ai_off_ramp.config import (
    AuditConfig, Contact, ContactMethod, EmailConfig, Escalation, Integrations,
    MessageTemplates, NtfyConfig, OffRampConfig, Privacy, UserProfile, load_config,
)


# ---------------------------------------------------------------- helpers

def _contact(cid="self", ntfy="alex-test-topic", priority=None, preferred="ntfy", **more):
    return Contact(
        id=cid, name="Alex" if cid == "self" else "Jordan", relationship=cid,
        methods=ContactMethod(ntfy=ntfy, **more), preferred_method=preferred,
        tiers=["check_in", "concerned", "urgent", "emergency"],
        visibility=["user_silent", "user_unwell"], ntfy_priority=priority,
    )


async def _with_fake_ntfy(status, coro_factory):
    """Run coro_factory(base_url) against a local fake ntfy; return (result, requests)."""
    seen = []

    async def handler(request):
        seen.append({"path": request.path, "headers": dict(request.headers),
                     "json": await request.json()})
        if status == 200:
            return web.json_response({"id": "abc", "event": "message"})
        return web.Response(status=status, text="nope")

    app = web.Application()
    app.router.add_post("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        result = await coro_factory(f"http://127.0.0.1:{port}")
    finally:
        await runner.cleanup()
    return result, seen


def _send(status, contact, tier, config_kwargs=None, subject="Subj", body="Body"):
    async def go(base):
        cfg = NtfyConfig(server=base, **(config_kwargs or {}))
        return await C.send_message(Integrations(ntfy=cfg), contact, subject, body, tier=tier)
    return asyncio.run(_with_fake_ntfy(status, go))


# ---------------------------------------------------------------- priority

@pytest.mark.parametrize("tier,expected", [
    ("check_in", 3), ("concerned", 4), ("urgent", 4), ("emergency", 5), (None, 3),
])
def test_tier_maps_to_ntfy_priority(tier, expected):
    assert C.ntfy_priority_for(_contact(), tier) == expected


def test_contact_priority_override_wins():
    """The 'wake ME up first' self contact is loud even at check_in."""
    assert C.ntfy_priority_for(_contact(priority=5), "check_in") == 5


# ---------------------------------------------------------------- the request

def test_publishes_json_to_server_root():
    result, seen = _send(200, _contact(), "emergency", subject="Please check on Alex", body="Plain body")
    assert result.success and result.method == "ntfy"
    assert len(seen) == 1
    req = seen[0]
    assert req["path"] == "/"
    assert req["json"] == {
        "topic": "alex-test-topic", "title": "Please check on Alex", "message": "Plain body",
        "priority": 5, "tags": ["rotating_light"],
    }
    assert "Authorization" not in req["headers"]
    assert result.details["ntfy_message_id"] == "abc"


def test_non_ascii_title_survives():
    title = "Checking in about José \U0001F49C"
    result, seen = _send(200, _contact(), "check_in", subject=title)
    assert seen[0]["json"]["title"] == title


def test_bearer_token_header():
    _, seen = _send(200, _contact(), "urgent", {"token": "tk_secret"})
    assert seen[0]["headers"]["Authorization"] == "Bearer tk_secret"


def test_basic_auth_header():
    _, seen = _send(200, _contact(), "urgent", {"username": "alex", "password": "pw"})
    expected = "Basic " + base64.b64encode(b"alex:pw").decode()
    assert seen[0]["headers"]["Authorization"] == expected


def test_full_url_topic_overrides_server():
    async def go(base):
        contact = _contact(ntfy=f"{base}/other-topic")
        cfg = NtfyConfig(server="https://ntfy.invalid")
        return await C.send_message(Integrations(ntfy=cfg), contact, "s", "b", tier="check_in")
    result, seen = asyncio.run(_with_fake_ntfy(200, go))
    assert result.success
    assert seen[0]["json"]["topic"] == "other-topic"


# ---------------------------------------------------------------- failure + fallback

def test_http_error_is_a_failure():
    result, _ = _send(403, _contact(), "urgent")
    assert not result.success
    assert "HTTP 403" in result.error


def test_ntfy_failure_falls_back_to_next_method(monkeypatch):
    sent = []

    async def fake_email(_cfg, addr, subject, body, contact):
        sent.append(addr)
        return C.SendResult(True, "email", contact.id, contact.name)

    monkeypatch.setattr(C, "send_email", fake_email)

    async def go(base):
        integ = Integrations(ntfy=NtfyConfig(server=base),
                             email=EmailConfig("smtp.test", 587, "u", "p", "f@test"))
        contact = _contact(email="jordan@test")
        return await C.send_message(integ, contact, "s", "b", tier="emergency")

    result, seen = asyncio.run(_with_fake_ntfy(500, go))
    assert len(seen) == 1                      # ntfy really was tried first
    assert result.success and result.method == "email"
    assert sent == ["jordan@test"]
    assert any(f.startswith("ntfy: HTTP 500") for f in result.details["earlier_failures"])


def test_unreachable_server_is_a_failure_not_a_crash():
    integ = Integrations(ntfy=NtfyConfig(server="http://127.0.0.1:9"))  # nothing listens on 9
    result = asyncio.run(C.send_message(integ, _contact(), "s", "b", tier="check_in"))
    assert not result.success and result.method == "ntfy"


# ---------------------------------------------------------------- privacy, end to end

def test_privacy_filter_applies_to_ntfy(tmp_path):
    from ai_off_ramp import server as S

    async def go(base):
        config = OffRampConfig(
            user=UserProfile(name="Alex", pronouns="she/her"),
            contacts=[_contact("partner")],
            privacy=Privacy(never_share=["substance_use"]),
            escalation=Escalation(),
            templates=MessageTemplates(),
            integrations=Integrations(ntfy=NtfyConfig(server=base)),
            audit=AuditConfig(log_file=str(tmp_path / "audit.jsonl")),
        )
        return await S._do_escalation(config, AuditLog(config.audit), tier="urgent",
                                      context_line="She was drinking before she drove",
                                      silence_duration="1 hour", ai_name="Ace")

    result, seen = asyncio.run(_with_fake_ntfy(200, go))
    assert result["contacts_notified"] == 1
    msg = seen[0]["json"]["message"]
    assert "drinking" not in msg
    assert "her wellbeing" in msg
    assert seen[0]["json"]["priority"] == 4


# ---------------------------------------------------------------- config

def _write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return p


BASE = """
user: {name: Alex}
contacts:
  - id: self
    name: Alex
    methods: {ntfy: "alex-long-random-topic"}
    preferred_method: ntfy
    tiers: [check_in]
    ntfy_priority: 5
"""


def test_ntfy_works_with_no_integrations_block(tmp_path):
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg.integrations.ntfy is not None
    assert cfg.integrations.ntfy.server == "https://ntfy.sh"
    assert cfg.contacts[0].ntfy_priority == 5


def test_token_read_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFRAMP_NTFY_TOKEN", "tk_from_env")
    cfg = load_config(_write(tmp_path, BASE + "integrations:\n  ntfy:\n    token: env:OFFRAMP_NTFY_TOKEN\n"))
    assert cfg.integrations.ntfy.token == "tk_from_env"


def test_literal_token_in_yaml_is_refused(tmp_path):
    with pytest.raises(ValueError, match="env: reference"):
        load_config(_write(tmp_path, BASE + "integrations:\n  ntfy:\n    token: tk_literal\n"))


def test_bad_priority_is_refused(tmp_path):
    with pytest.raises(ValueError, match="ntfy_priority"):
        load_config(_write(tmp_path, BASE.replace("ntfy_priority: 5", "ntfy_priority: 9")))
