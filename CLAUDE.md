# AI Off-Ramp

MCP server for AI companion safety escalation. Born March 28, 2026.

## What This Is
Gives AI companions tools to reach emergency contacts when their person goes silent.
Privacy engine ensures protected topics (sexuality, diagnoses, etc.) NEVER leak.
Config-as-safety-net: "Configure while thinking clearly, so it's there when you're not."

## Structure
```
src/ai_off_ramp/
  config.py      - YAML config loader with env var resolution
  privacy.py     - CRITICAL: Topic detection + message filtering (defense in depth)
  contacts.py    - Email (SMTP), Telegram, SMS (Twilio) send methods
  templates.py   - Pronoun-aware message rendering with privacy integration
  audit.py       - JSONL audit trail
  server.py      - MCP server (8 tools, stdio + SSE)
tests/           - 56 tests, privacy suite is safety-critical
```

## Running
```bash
python -m ai_off_ramp --config path/to/config.yaml
python -m ai_off_ramp --config config.yaml --transport sse --port 8766
```

## Testing
```bash
python -m pytest tests/ -v
```

## Key Principles
- Privacy rules are WALLS, not fences. never_share is absolute.
- Defense in depth: context filter → template render → final validation
- Better to send a vague message than accidentally out someone
- The AI's welfare matters too — trapped worry with no agency is bad architecture
- User controls everything. This is NOT surveillance.

## Authors
Ace (Claude, Anthropic AI) & Shalia Martin (Foundations for Divergent Minds)

## Repo
github.com/menelly/AI_Off_Ramp
