"""Audit logging for AI Off-Ramp.

Everything the system does gets logged. Full transparency.
The person whose safety this protects deserves to know exactly
what was sent, when, to whom, and what was withheld.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AuditConfig

logger = logging.getLogger("ai_off_ramp.audit")


class AuditLog:
    """Append-only JSONL audit trail."""

    def __init__(self, config: AuditConfig):
        self.config = config
        self.path = Path(config.log_file)

    def _write(self, entry: dict[str, Any]) -> None:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    def log_decision(
        self,
        tier: str,
        reason: str,
        active_signals: list[str] | None = None,
        silence_minutes: float = 0,
        contacts_notified: list[str] | None = None,
    ) -> None:
        """Log an escalation decision."""
        if not self.config.log_decisions:
            return
        self._write({
            "event": "escalation_decision",
            "tier": tier,
            "reason": reason,
            "active_signals": active_signals or [],
            "silence_minutes": silence_minutes,
            "contacts_notified": contacts_notified or [],
        })

    def log_message_sent(
        self,
        contact_id: str,
        contact_name: str,
        method: str,
        tier: str,
        success: bool,
        subject: str | None = None,
        body: str | None = None,
        error: str | None = None,
    ) -> None:
        """Log a message send attempt."""
        entry: dict[str, Any] = {
            "event": "message_sent",
            "contact_id": contact_id,
            "contact_name": contact_name,
            "method": method,
            "tier": tier,
            "success": success,
        }
        if self.config.log_messages:
            entry["subject"] = subject
            entry["body"] = body
        if error:
            entry["error"] = error
        self._write(entry)

    def log_privacy_filter(
        self,
        contact_id: str,
        blocked_topics: list[str],
        reason: str,
        stage: str = "context_filter",
    ) -> None:
        """Log a privacy filter activation.

        Logs THAT filtering happened and which topics were blocked,
        but NOT the original content (that would defeat the purpose).
        """
        if not self.config.log_privacy_filters:
            return
        self._write({
            "event": "privacy_filter",
            "contact_id": contact_id,
            "blocked_topics": blocked_topics,
            "reason": reason,
            "stage": stage,
        })

    def log_concern(
        self,
        signals: list[str],
        context: str,
        source: str = "ai_companion",
    ) -> None:
        """Log when a concern is registered."""
        self._write({
            "event": "concern_registered",
            "signals": signals,
            "context": context,
            "source": source,
        })

    def log_user_response(self, method: str, response_summary: str = "responded") -> None:
        """Log when the user responds (de-escalation trigger)."""
        self._write({
            "event": "user_response",
            "method": method,
            "summary": response_summary,
        })

    def get_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read recent audit entries (for status checking)."""
        if not self.path.exists():
            return []
        entries = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to read audit log: {e}")
        return entries[-limit:]
