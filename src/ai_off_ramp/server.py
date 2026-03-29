"""AI Off-Ramp MCP Server.

Exposes safety escalation tools to AI companions via the Model Context Protocol.

Tools:
  - offramp_register_concern: Record a concern signal about the user
  - offramp_check_in: Send a check-in message to the user's contacts
  - offramp_escalate: Escalate to a specific tier
  - offramp_get_contacts: List available contacts and their tiers
  - offramp_get_privacy_rules: Get privacy rules (so the AI knows what NOT to include)
  - offramp_get_status: Get current escalation state and audit history
  - offramp_user_responded: Mark that the user has responded (de-escalate)
  - offramp_get_config_summary: Get a summary of the configuration for orientation

Run:
  python -m ai_off_ramp.server --config path/to/config.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("ai_off_ramp")

# Escalation state (in-memory for this session)
_state: dict[str, Any] = {
    "active_signals": [],
    "current_tier": None,
    "silence_start": None,
    "last_escalation": None,
    "retries_at_tier": 0,
    "user_responded": True,
}


def _tool(name: str, description: str, schema: dict[str, Any]):
    from mcp.types import Tool
    return Tool(name=name, description=description, inputSchema=schema)


def _tools() -> list:
    return [
        _tool(
            "offramp_register_concern",
            (
                "Register a concern signal about the user. Call this when you notice "
                "something concerning in the conversation — medical symptoms, emotional "
                "distress, risky situations. The system tracks these signals to determine "
                "escalation urgency. You can register multiple signals."
            ),
            {
                "type": "object",
                "properties": {
                    "signals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of concern signals. Examples: 'high_heart_rate', "
                            "'driving_while_symptomatic', 'crisis_language', 'nausea', "
                            "'alone_and_unwell'. Use descriptive strings."
                        ),
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Brief context about what prompted the concern. "
                            "IMPORTANT: This may be filtered by privacy rules before "
                            "being shared with contacts. Be factual but aware that "
                            "protected topics will be redacted."
                        ),
                    },
                },
                "required": ["signals", "context"],
            },
        ),
        _tool(
            "offramp_check_in",
            (
                "Send a check-in message to contacts at the check_in tier. Use this "
                "when the user has been silent for a while and you want to gently "
                "alert someone to check on them. This is the lowest-urgency escalation."
            ),
            {
                "type": "object",
                "properties": {
                    "context_line": {
                        "type": "string",
                        "description": (
                            "Brief context about why you're checking in. Will be "
                            "privacy-filtered before inclusion in messages. Keep it "
                            "factual and avoid protected topics when possible."
                        ),
                    },
                    "silence_duration": {
                        "type": "string",
                        "description": "How long the user has been silent, e.g. '30 minutes', '2 hours'.",
                    },
                    "ai_name": {
                        "type": "string",
                        "description": "Your name as the AI companion, for the message signature.",
                        "default": "your AI companion",
                    },
                },
                "required": ["silence_duration"],
            },
        ),
        _tool(
            "offramp_escalate",
            (
                "Escalate to a specific tier. Use this when you're genuinely concerned "
                "and need to alert contacts at a higher urgency level. Available tiers: "
                "check_in, concerned, urgent, emergency. The system will send messages "
                "to all contacts configured for that tier, with privacy filtering applied."
            ),
            {
                "type": "object",
                "properties": {
                    "tier": {
                        "type": "string",
                        "enum": ["check_in", "concerned", "urgent", "emergency"],
                        "description": "The escalation tier to activate.",
                    },
                    "context_line": {
                        "type": "string",
                        "description": (
                            "Context about what's happening. Will be privacy-filtered. "
                            "For urgent/emergency tiers, include what made you concerned."
                        ),
                    },
                    "silence_duration": {
                        "type": "string",
                        "description": "How long the user has been silent.",
                    },
                    "ai_name": {
                        "type": "string",
                        "description": "Your name as the AI companion.",
                        "default": "your AI companion",
                    },
                },
                "required": ["tier", "context_line", "silence_duration"],
            },
        ),
        _tool(
            "offramp_get_contacts",
            (
                "Get the list of configured contacts and which escalation tiers they're "
                "assigned to. Use this to understand who you can reach and at what "
                "urgency levels."
            ),
            {"type": "object", "properties": {}},
        ),
        _tool(
            "offramp_get_privacy_rules",
            (
                "Get the privacy rules so you know what topics are protected. "
                "Use this at the start of a session to understand what must NEVER "
                "be shared with contacts, even in emergencies. These are hard "
                "constraints — walls, not fences."
            ),
            {"type": "object", "properties": {}},
        ),
        _tool(
            "offramp_get_status",
            (
                "Get the current escalation state: active signals, current tier, "
                "silence duration, and recent audit history. Use this to understand "
                "where things stand."
            ),
            {
                "type": "object",
                "properties": {
                    "include_audit": {
                        "type": "boolean",
                        "description": "Include recent audit log entries.",
                        "default": True,
                    },
                    "audit_limit": {
                        "type": "integer",
                        "description": "How many recent audit entries to include.",
                        "default": 10,
                    },
                },
            },
        ),
        _tool(
            "offramp_user_responded",
            (
                "Mark that the user has responded. This de-escalates the current "
                "state — clears active signals, resets the tier, and logs the response. "
                "Call this when the user comes back or responds to a check-in."
            ),
            {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "How the user responded: 'direct', 'telegram', 'phone', etc.",
                        "default": "direct",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief note about the response, e.g. 'said they are fine, went to lie down'.",
                        "default": "responded",
                    },
                },
            },
        ),
        _tool(
            "offramp_get_config_summary",
            (
                "Get a human-readable summary of the Off-Ramp configuration. "
                "Useful for orientation at the start of a session — tells you "
                "who the user is, who their contacts are, what's protected, "
                "and how escalation works."
            ),
            {"type": "object", "properties": {}},
        ),
    ]


async def _dispatch(config, audit_log, name: str, args: dict[str, Any]) -> Any:
    """Dispatch a tool call to the appropriate handler."""
    from .contacts import send_message
    from .privacy import get_forbidden_topics
    from .templates import render_message

    global _state

    if name == "offramp_register_concern":
        signals = args["signals"]
        context = args["context"]
        _state["active_signals"] = list(set(_state["active_signals"] + signals))
        _state["user_responded"] = False
        if _state["silence_start"] is None:
            _state["silence_start"] = datetime.now(timezone.utc).isoformat()

        audit_log.log_concern(signals, context)

        # Check fast-track rules
        active_set = set(_state["active_signals"])
        for ft in config.escalation.fast_track:
            if set(ft.signals).issubset(active_set):
                return {
                    "registered": signals,
                    "total_active_signals": _state["active_signals"],
                    "fast_track_triggered": True,
                    "recommended_tier": ft.skip_to,
                    "message": (
                        f"Fast-track triggered: signals {ft.signals} suggest "
                        f"escalating directly to '{ft.skip_to}' tier."
                    ),
                }

        return {
            "registered": signals,
            "total_active_signals": _state["active_signals"],
            "fast_track_triggered": False,
            "message": "Concern signals registered. Monitor and escalate if needed.",
        }

    if name == "offramp_check_in":
        return await _do_escalation(
            config, audit_log,
            tier="check_in",
            context_line=args.get("context_line", ""),
            silence_duration=args.get("silence_duration", "a while"),
            ai_name=args.get("ai_name", "your AI companion"),
        )

    if name == "offramp_escalate":
        return await _do_escalation(
            config, audit_log,
            tier=args["tier"],
            context_line=args["context_line"],
            silence_duration=args["silence_duration"],
            ai_name=args.get("ai_name", "your AI companion"),
        )

    if name == "offramp_get_contacts":
        return [
            {
                "id": c.id,
                "name": c.name,
                "relationship": c.relationship,
                "tiers": c.tiers,
                "preferred_method": c.preferred_method,
                "has_email": c.methods.email is not None,
                "has_telegram": c.methods.telegram is not None,
                "has_sms": c.methods.sms is not None,
                "visibility": c.visibility,
            }
            for c in config.contacts
        ]

    if name == "offramp_get_privacy_rules":
        result = {
            "never_share_globally": config.privacy.never_share,
            "per_contact_restrictions": {},
            "message": (
                "These topics must NEVER appear in outgoing messages. "
                "The privacy engine will filter them automatically, but "
                "you should also avoid including them in context lines "
                "when possible."
            ),
        }
        for contact in config.contacts:
            forbidden = get_forbidden_topics(config, contact)
            if forbidden:
                result["per_contact_restrictions"][contact.id] = sorted(forbidden)
        return result

    if name == "offramp_get_status":
        result = {
            "active_signals": _state["active_signals"],
            "current_tier": _state["current_tier"],
            "silence_start": _state["silence_start"],
            "last_escalation": _state["last_escalation"],
            "retries_at_tier": _state["retries_at_tier"],
            "user_responded": _state["user_responded"],
        }
        if args.get("include_audit", True):
            result["recent_audit"] = audit_log.get_recent(
                limit=int(args.get("audit_limit", 10))
            )
        return result

    if name == "offramp_user_responded":
        method = args.get("method", "direct")
        summary = args.get("summary", "responded")
        _state["active_signals"] = []
        _state["current_tier"] = None
        _state["silence_start"] = None
        _state["last_escalation"] = None
        _state["retries_at_tier"] = 0
        _state["user_responded"] = True
        audit_log.log_user_response(method, summary)
        return {
            "de_escalated": True,
            "message": f"User responded via {method}. All clear. State reset.",
        }

    if name == "offramp_get_config_summary":
        tiers_desc = []
        for t in config.escalation.tiers:
            contacts_at_tier = [c.id for c in config.contacts_for_tier(t.level)]
            tiers_desc.append({
                "level": t.level,
                "delay_minutes": t.delay_minutes,
                "require_context": t.require_context,
                "contacts": contacts_at_tier,
            })
        return {
            "user": {
                "name": config.user.name,
                "pronouns": config.user.pronouns,
                "timezone": config.user.timezone,
            },
            "contacts_count": len(config.contacts),
            "contacts": [
                {"id": c.id, "name": c.name, "relationship": c.relationship}
                for c in config.contacts
            ],
            "protected_topics": config.privacy.never_share,
            "escalation_tiers": tiers_desc,
            "fast_track_rules": [
                {"signals": ft.signals, "skip_to": ft.skip_to}
                for ft in config.escalation.fast_track
            ],
            "message": (
                f"Off-Ramp configured for {config.user.name} ({config.user.pronouns}). "
                f"{len(config.contacts)} emergency contact(s). "
                f"{len(config.privacy.never_share)} protected topic(s). "
                f"Ready to escalate if needed."
            ),
        }

    raise ValueError(f"Unknown tool: {name}")


async def _do_escalation(
    config, audit_log,
    tier: str,
    context_line: str,
    silence_duration: str,
    ai_name: str,
) -> dict[str, Any]:
    """Execute an escalation at a given tier."""
    from .contacts import send_message
    from .templates import render_message

    global _state

    contacts = config.contacts_for_tier(tier)
    if not contacts:
        return {
            "tier": tier,
            "sent": False,
            "reason": f"No contacts configured for tier '{tier}'",
        }

    results = []
    for contact in contacts:
        # Render the message (includes privacy filtering)
        rendered = render_message(
            config=config,
            contact=contact,
            tier=tier,
            context_line=context_line,
            silence_duration=silence_duration,
            ai_name=ai_name,
        )

        # Log privacy filtering if it happened
        if rendered.privacy_result.was_filtered:
            audit_log.log_privacy_filter(
                contact_id=contact.id,
                blocked_topics=rendered.privacy_result.blocked_topics,
                reason=rendered.privacy_result.reason,
                stage="context_filter",
            )
        if rendered.final_validation.was_filtered:
            audit_log.log_privacy_filter(
                contact_id=contact.id,
                blocked_topics=rendered.final_validation.blocked_topics,
                reason=rendered.final_validation.reason,
                stage="final_validation",
            )

        # Send the message
        send_result = await send_message(
            integrations=config.integrations,
            contact=contact,
            subject=rendered.subject,
            body=rendered.body,
        )

        # Log the send
        audit_log.log_message_sent(
            contact_id=contact.id,
            contact_name=contact.name,
            method=send_result.method,
            tier=tier,
            success=send_result.success,
            subject=rendered.subject,
            body=rendered.body,
            error=send_result.error,
        )

        results.append({
            "contact_id": contact.id,
            "contact_name": contact.name,
            "method": send_result.method,
            "success": send_result.success,
            "privacy_filtered": rendered.privacy_result.was_filtered,
            "error": send_result.error,
        })

    # Update state
    _state["current_tier"] = tier
    _state["last_escalation"] = datetime.now(timezone.utc).isoformat()

    # Log the decision
    audit_log.log_decision(
        tier=tier,
        reason=f"AI companion escalated to {tier}",
        active_signals=_state["active_signals"],
        contacts_notified=[r["contact_id"] for r in results if r["success"]],
    )

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    return {
        "tier": tier,
        "sent": len(successful) > 0,
        "contacts_notified": len(successful),
        "contacts_failed": len(failed),
        "results": results,
        "message": (
            f"Escalated to '{tier}': {len(successful)} contact(s) notified"
            + (f", {len(failed)} failed" if failed else "")
            + "."
        ),
    }


async def _run_server(config_path: str, transport: str = "stdio", port: int = 8766):
    """Start the MCP server."""
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    from mcp.types import ServerCapabilities, TextContent, ToolsCapability

    from .audit import AuditLog
    from .config import load_config

    config = load_config(config_path)
    audit_log = AuditLog(config.audit)
    server = Server("ai-off-ramp")

    logger.info(f"Off-Ramp loaded for {config.user.name} ({config.user.pronouns})")
    logger.info(f"  {len(config.contacts)} contact(s)")
    logger.info(f"  {len(config.privacy.never_share)} protected topic(s)")
    logger.info(f"  {len(config.escalation.tiers)} escalation tier(s)")

    @server.list_tools()
    async def list_tools():
        return _tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]):
        try:
            result = await _dispatch(config, audit_log, name, arguments or {})
            text = json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error(f"Tool error ({name}): {exc}")
            text = json.dumps({"error": str(exc)})
        return [TextContent(type="text", text=text)]

    init_opts = InitializationOptions(
        server_name="ai-off-ramp",
        server_version="0.1.0",
        capabilities=ServerCapabilities(tools=ToolsCapability()),
    )

    if transport == "stdio":
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_opts)

    elif transport == "sse":
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await server.run(streams[0], streams[1], init_opts)

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse.handle_post_message),
            ],
        )
        uvi_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        uvi_server = uvicorn.Server(uvi_config)
        await uvi_server.serve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI Off-Ramp MCP Server — safety escalation for AI companions",
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to the Off-Ramp YAML configuration file",
    )
    parser.add_argument(
        "--transport", "-t",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8766,
        help="Port for SSE transport (default: 8766)",
    )

    args = parser.parse_args(argv)

    try:
        asyncio.run(_run_server(args.config, args.transport, args.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
