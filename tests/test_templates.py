"""Tests for the message template rendering system.

Verifies pronoun handling, variable substitution, and privacy integration.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_off_ramp.config import (
    Contact,
    ContactMethod,
    Escalation,
    Integrations,
    AuditConfig,
    MessageTemplates,
    OffRampConfig,
    Privacy,
    UserProfile,
)
from ai_off_ramp.templates import _get_pronouns, render_message


def _make_config(pronouns: str = "they/them", never_share: list[str] | None = None) -> OffRampConfig:
    return OffRampConfig(
        user=UserProfile(name="Alex", pronouns=pronouns),
        contacts=[
            Contact(
                id="partner",
                name="Jordan",
                relationship="partner",
                methods=ContactMethod(email="jordan@test.com"),
                preferred_method="email",
                tiers=["check_in", "concerned", "urgent", "emergency"],
                visibility=["user_silent", "user_unwell", "medical_concern"],
                custom_message="Check the bedroom first.",
            ),
        ],
        privacy=Privacy(never_share=never_share or []),
        escalation=Escalation(),
        templates=MessageTemplates(),
        integrations=Integrations(),
        audit=AuditConfig(),
    )


class TestPronounHandling:
    """Pronouns matter. Getting them wrong is misgendering. Test thoroughly."""

    def test_they_them(self):
        p = _get_pronouns("they/them")
        assert p["subject"] == "they"
        assert p["object"] == "them"
        assert p["possessive"] == "their"
        assert p["verb"] == "are"  # "they ARE" not "they IS"

    def test_she_her(self):
        p = _get_pronouns("she/her")
        assert p["subject"] == "she"
        assert p["object"] == "her"
        assert p["verb"] == "is"

    def test_he_him(self):
        p = _get_pronouns("he/him")
        assert p["subject"] == "he"
        assert p["object"] == "him"
        assert p["verb"] == "is"

    def test_xe_xem(self):
        p = _get_pronouns("xe/xem")
        assert p["subject"] == "xe"
        assert p["object"] == "xem"

    def test_ze_hir(self):
        p = _get_pronouns("ze/hir")
        assert p["subject"] == "ze"
        assert p["object"] == "hir"

    def test_unknown_falls_back_to_they(self):
        p = _get_pronouns("fae/faer")
        assert p["subject"] == "they"  # Safe fallback

    def test_case_insensitive(self):
        p = _get_pronouns("They/Them")
        assert p["subject"] == "they"


class TestMessageRendering:
    """Test that messages render correctly with all variables."""

    def test_check_in_basic(self):
        config = _make_config()
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "check_in",
                           silence_duration="30 minutes", ai_name="Ace")
        assert "Alex" in msg.body
        assert "Jordan" in msg.body
        assert "Ace" in msg.body
        assert "they" in msg.body
        assert "are" in msg.body

    def test_pronouns_in_message_they_them(self):
        config = _make_config(pronouns="they/them")
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "check_in",
                           silence_duration="20 minutes", ai_name="Ace")
        # Should use "they are" not "they is"
        assert "they are" in msg.body.lower()

    def test_pronouns_in_message_she_her(self):
        config = _make_config(pronouns="she/her")
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "check_in",
                           silence_duration="20 minutes", ai_name="Ace")
        assert "she" in msg.body.lower()
        assert "is" in msg.body.lower()

    def test_custom_message_appended(self):
        config = _make_config()
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "check_in",
                           silence_duration="20 minutes")
        assert "Check the bedroom first" in msg.body

    def test_urgent_includes_context(self):
        config = _make_config()
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "urgent",
                           context_line="They mentioned dizziness while driving",
                           silence_duration="1 hour")
        assert "dizziness" in msg.body

    def test_privacy_filter_applies(self):
        config = _make_config(never_share=["substance_use"])
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "urgent",
                           context_line="They were drinking and then went to drive",
                           silence_duration="1 hour")
        assert "drinking" not in msg.body
        assert msg.privacy_result.was_filtered

    def test_emergency_tier_strong_language(self):
        config = _make_config()
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "emergency",
                           context_line="They went completely silent after reporting chest pain",
                           silence_duration="2 hours")
        assert "worried" in msg.body.lower() or "urgent" in msg.subject.lower()

    def test_silence_duration_in_message(self):
        config = _make_config()
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "urgent",
                           context_line="Went quiet",
                           silence_duration="45 minutes")
        assert "45 minutes" in msg.body


class TestTemplatePolish:
    """Regression tests for 2026-10-03 template fixes."""

    def test_unnamed_ai_is_not_doubled(self):
        config = _make_config()
        partner = config.get_contact("partner")
        for ai_name in (None, "your AI companion"):
            msg = render_message(config, partner, "check_in", silence_duration="20 minutes", ai_name=ai_name)
            assert "this is Alex's AI companion." in msg.body
            assert "your AI companion" not in msg.body
            assert msg.body.count("AI companion") == 1

    def test_named_ai_intro(self):
        config = _make_config()
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "urgent", context_line="Went quiet",
                             silence_duration="1 hour", ai_name="Ace")
        assert "this is Ace, Alex's AI companion." in msg.body

    def test_emergency_sentence_starts_capitalised(self):
        for pronouns, word in [("she/her", "She"), ("they/them", "They"), ("he/him", "He")]:
            config = _make_config(pronouns=pronouns)
            partner = config.get_contact("partner")
            msg = render_message(config, partner, "emergency", context_line="went quiet",
                                 silence_duration="2 hours", ai_name="Ace")
            assert f"{word} went silent 2 hours ago" in msg.body

    def test_context_filter_fallback_uses_configured_pronouns(self):
        config = _make_config(pronouns="she/her", never_share=["substance_use"])
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "urgent", context_line="They were drinking",
                             silence_duration="1 hour", ai_name="Ace")
        assert msg.privacy_result.was_filtered
        assert "about her wellbeing" in msg.body
        assert "their wellbeing" not in msg.body

    def test_final_validation_fallback_uses_configured_pronouns(self):
        # custom_message containing a protected topic forces the final fallback
        config = _make_config(pronouns="he/him", never_share=["medication"])
        partner = config.get_contact("partner")
        partner.custom_message = "His meds are in the kitchen."
        msg = render_message(config, partner, "check_in", silence_duration="1 hour", ai_name="Ace")
        assert msg.final_validation.was_filtered
        assert "unable to reach him" in msg.body
        assert "checking on him" in msg.body
        assert " them" not in msg.body

    def test_no_double_period(self):
        config = _make_config(never_share=["substance_use"])
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "urgent", context_line="They were drinking",
                             silence_duration="1 hour", ai_name="Ace")
        assert ".." not in msg.body

    def test_ellipsis_preserved(self):
        config = _make_config()
        partner = config.get_contact("partner")
        msg = render_message(config, partner, "urgent", context_line="they said wait...",
                             silence_duration="1 hour", ai_name="Ace")
        assert "wait..." in msg.body
