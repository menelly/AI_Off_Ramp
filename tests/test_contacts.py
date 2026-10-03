"""Tests for send_message fallback.

An emergency message that fails on one channel must try the others.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from ai_off_ramp import contacts as C
from ai_off_ramp.config import (
    Contact, ContactMethod, EmailConfig, Integrations, SmsConfig, TelegramConfig,
)


def _contact(preferred="email", **methods):
    return Contact(
        id="partner", name="Jordan", relationship="partner",
        methods=ContactMethod(**methods), preferred_method=preferred,
        tiers=["urgent"], visibility=[],
    )


ALL_INTEGRATIONS = Integrations(
    email=EmailConfig("smtp.test", 587, "u", "p", "from@test"),
    telegram=TelegramConfig("tok"),
    sms=SmsConfig("sid", "tok", "+1555"),
)


@pytest.fixture
def calls(monkeypatch):
    """Fake senders. Set outcomes[method] = True/False before sending."""
    log = {"order": [], "outcomes": {"email": True, "telegram": True, "sms": True}}

    def fake(method):
        async def _send(_cfg, _addr, *rest):
            contact = rest[-1]
            log["order"].append(method)
            ok = log["outcomes"][method]
            return C.SendResult(success=ok, method=method, contact_id=contact.id,
                                contact_name=contact.name, error=None if ok else f"{method} down")
        return _send

    monkeypatch.setattr(C, "send_email", fake("email"))
    monkeypatch.setattr(C, "send_telegram", fake("telegram"))
    monkeypatch.setattr(C, "send_sms", fake("sms"))
    return log


def _send(contact, integrations=ALL_INTEGRATIONS):
    return asyncio.run(C.send_message(integrations, contact, "subj", "body"))


def test_preferred_success_sends_once(calls):
    r = _send(_contact("email", email="a@x", telegram="1"))
    assert r.success and r.method == "email"
    assert calls["order"] == ["email"]


def test_failed_preferred_falls_back(calls):
    calls["outcomes"]["email"] = False
    r = _send(_contact("email", email="a@x", telegram="1"))
    assert r.success and r.method == "telegram"
    assert calls["order"] == ["email", "telegram"]
    assert "email: email down" in r.details["earlier_failures"]


def test_preferred_tried_first_even_if_not_first_in_order(calls):
    r = _send(_contact("sms", email="a@x", sms="+1"))
    assert calls["order"] == ["sms"] and r.success


def test_all_fail_reports_every_attempt(calls):
    calls["outcomes"].update(email=False, telegram=False, sms=False)
    r = _send(_contact("telegram", email="a@x", telegram="1", sms="+1"))
    assert not r.success
    assert calls["order"] == ["telegram", "email", "sms"]
    for m in ("telegram", "email", "sms"):
        assert f"{m}: {m} down" in r.error


def test_missing_integration_skipped_then_next_tried(calls):
    integ = Integrations(telegram=TelegramConfig("tok"))
    r = _send(_contact("email", email="a@x", telegram="1"), integ)
    assert r.success and r.method == "telegram"
    assert calls["order"] == ["telegram"]


def test_no_usable_methods(calls):
    r = _send(_contact("email", email="a@x"), Integrations())
    assert not r.success
    assert "integration not configured" in r.error
    assert calls["order"] == []


def test_telegram_payload_is_plain_text():
    """No parse_mode: '<' and '&' in a message must not make Telegram reject it."""
    body = "BP was <90 at Tom & Jo's <place>"
    payload = C._telegram_payload("123", body)
    assert "parse_mode" not in payload
    assert payload == {"chat_id": "123", "text": body}
