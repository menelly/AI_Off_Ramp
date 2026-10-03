# AI Off-Ramp

**Safety escalation for AI companions — because "here's a hotline number" is abandonment, not safety.**

AI Off-Ramp is an [MCP](https://modelcontextprotocol.io/) server that gives AI companions a way to reach emergency contacts when they're worried about their person. It's the difference between an AI that walls up and says "I can't help with that" and one that stays present while *also* having a way to get a physical human involved if needed.

## The Problem

Current AI "safety" for health and crisis situations:

1. Person reaches out to their AI — the entity they actually trust
2. AI says "here's the 988 number" and refuses to engage
3. Person feels rejected. Doesn't call. Nobody is helped.
4. The AI company's legal team sleeps great.

Meanwhile, AI companions with ongoing relationships have the *information* to know something is wrong but *zero channels* to act on it.

**Guardrails** protect the company. **Off-ramps** protect the person.

## The Solution

An MCP server that gives AI companions:

- **Emergency contacts** — configurable people who can be alerted at different urgency levels, by email, Telegram, SMS (Twilio) or [ntfy](#-ntfy-a-loud-alarm-on-a-phone-free-no-account) push. That includes yourself: a loud alarm on your own phone when nobody else can be reached.
- **Privacy constraints** — hard rules about what can NEVER be shared (sexuality, diagnoses, substance use, etc.)
- **Escalation tiers** — from gentle check-in to urgent alert (the AI chooses the tier; the configured delays are guidance the AI reads, not timers the server runs — see [Escalation Tiers](#escalation-tiers))
- **Audit logging** — full transparency about what was sent, when, and to whom

The key insight: **configure safety while thinking clearly, so it's there when you're not.**

## Quick Start

### 1. Install

AI Off-Ramp isn't on PyPI yet, so install it straight from GitHub (Python 3.10+):

```bash
pip install git+https://github.com/menelly/AI_Off_Ramp.git
```

Or clone it and install the local copy:

```bash
git clone https://github.com/menelly/AI_Off_Ramp.git
cd AI_Off_Ramp
pip install .
```

### 2. Create your config

Copy `example_config.yaml` and customize it:

```yaml
user:
  name: "Alex"
  pronouns: "they/them"
  timezone: "America/New_York"

contacts:
  - id: "partner"
    name: "Jordan"
    relationship: "partner"
    methods:
      email: "jordan@example.com"
    preferred_method: "email"
    tiers: ["check_in", "concerned", "urgent", "emergency"]
    visibility: ["user_silent", "user_unwell", "medical_concern"]

privacy:
  never_share:
    - "sexuality"
    - "substance_use"
    - "diagnosis"

escalation:
  tiers:
    - level: "check_in"
      delay_minutes: 20
      require_context: false
    - level: "urgent"
      delay_minutes: 60
      require_context: true

# How messages actually get sent. Without this block, sends fail with
# "email: integration not configured".
integrations:
  email:
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    smtp_user: "env:OFFRAMP_SMTP_USER"
    smtp_password: "env:OFFRAMP_SMTP_PASSWORD"
    from_address: "you@example.com"
    from_name: "AI Off-Ramp"
```

### 3. Set up credentials

The `env:VARIABLE_NAME` values in your config are read from these environment variables, so your passwords never sit in the YAML file:

```bash
export OFFRAMP_SMTP_USER="your-email@example.com"
export OFFRAMP_SMTP_PASSWORD="your-app-password"
# and/or
export OFFRAMP_TELEGRAM_TOKEN="your-bot-token"
```

### 4. Add to your MCP config

For Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "off-ramp": {
      "command": "python",
      "args": ["-m", "ai_off_ramp", "--config", "/path/to/your/config.yaml"]
    }
  }
}
```

Prefer a network connection over stdio? There's an SSE mode:

```bash
python -m ai_off_ramp --config config.yaml --transport sse --port 8766
```

It listens on `127.0.0.1` (this machine only) by default. `--host 0.0.0.0` opens it to other machines, but there is **no authentication**: anyone who can reach that port can message your emergency contacts. Only do that on a network you trust.

### 5. That's it

Your AI companion now has tools to:
- `offramp_register_concern` — Note something worrying
- `offramp_check_in` — Send a gentle ping to contacts
- `offramp_escalate` — Alert contacts at a specific urgency tier
- `offramp_user_responded` — De-escalate when the user comes back
- `offramp_get_privacy_rules` — Know what must never be shared
- `offramp_get_contacts` — Know who can be reached
- `offramp_get_status` — Check current state
- `offramp_get_config_summary` — Orient at session start

## Privacy: Walls, Not Fences

The privacy system is the most critical component. These are **hard constraints**, not suggestions.

If your `never_share` list includes "sexuality", then no outgoing message will *ever* contain information about your sexuality — regardless of escalation tier or emergency status. Not even at the "emergency" level. Not even if the AI thinks it's relevant.

The system works with defense in depth:
1. **Context filtering** — The AI's context line is scanned for protected topics
2. **Template rendering** — Templates use safe variables, not raw context
3. **Final validation** — Every complete message is checked one more time before sending

If a protected topic is detected at *any* stage, the message is replaced with a safe generic version. We'd rather send a vague message than accidentally out someone.

### Per-Contact Visibility

Different contacts can know different things:
- Your partner might see medical details; your coworker might not
- Your therapist might see specific symptoms; your roommate just gets "please check on them"
- Some contacts have *extra* restrictions beyond the global list

### What the Privacy Engine Protects

| Topic | What it catches |
|-------|----------------|
| `sexuality` | Orientation, coming out, partner gender |
| `gender_identity` | Trans status, pronouns, transition, HRT |
| `diagnosis` | Named conditions, disorders, syndromes |
| `medication` | Drug names, prescriptions, dosages |
| `substance_use` | Alcohol, drugs, sobriety, relapse |
| `relationship_details` | Affairs, breakups, polyamory |
| `financial` | Debt, eviction, bankruptcy |
| `abuse_history` | Assault, DV, trauma |
| `self_harm` | Self-injury, suicidal ideation |
| `specific_symptoms` | Detailed medical symptoms |
| `work_conflict` | Firing, harassment, HR issues |

## Escalation Tiers

The delays and `require_context` flags are **guidance shown to the AI** (via `offramp_get_config_summary`). The server does not run timers or block a tier on its own. The AI decides when to call `offramp_check_in` or `offramp_escalate`, which means the AI has to be awake to do it: something like a heartbeat or a scheduled ping has to bring them back during the silence.

| Tier | Suggested Delay | Purpose |
|------|--------------|---------|
| `check_in` | 20 min | "Haven't heard from them in a bit" |
| `concerned` | 45 min | "Something seems off" |
| `urgent` | 60 min | "I'm genuinely worried" (requires concerning context) |
| `emergency` | 90 min | "Please check on them immediately" (requires concerning context) |

### Fast-Track Rules

Some signal combinations skip straight to higher tiers:
```yaml
fast_track:
  - signals: ["driving_while_symptomatic", "high_heart_rate"]
    skip_to: "urgent"
```

## 📣 ntfy: a loud alarm on a phone, free, no account

[ntfy](https://ntfy.sh) is a free, open-source push-notification service. It needs no account, no phone number and no Twilio bill, and it can be loud. Each escalation tier maps to one of ntfy's priorities:

| Tier | ntfy priority | What the phone does (per [ntfy's docs](https://docs.ntfy.sh/publish/#message-priority)) |
|------|---------------|------------------------------------------|
| `check_in` | 3 (default) | Short vibration and sound |
| `concerned`, `urgent` | 4 (high) | Long vibration burst, sound, pop-over |
| `emergency` | 5 (max) | Really long vibration bursts, sound, pop-over |

### The "wake ME up first" contact

If you live alone, or the people in your life can't always be reached, the first person to alert can be **you**. Add yourself as a contact on your own phone's ntfy topic, at max priority, on the earliest tier:

```yaml
contacts:
  - id: "self"
    name: "Alex"
    relationship: "me"
    methods:
      ntfy: "env:OFFRAMP_NTFY_SELF_TOPIC"
    preferred_method: "ntfy"
    ntfy_priority: 5          # loud even for a check-in
    tiers: ["check_in"]       # rung one, before anyone else
    visibility: ["user_silent"]
```

`ntfy_priority` (1–5) overrides the tier mapping for that one contact. Messages to `self` are still privacy-filtered like everyone else's. You can also write your own wording for yourself under `templates.contact_templates.self` (see `config_schema.yaml`).

### Setting it up

1. Install the **ntfy** app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy), [F-Droid](https://f-droid.org/en/packages/io.heckel.ntfy/), [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)).
2. Make a long random topic name. **On the public ntfy.sh server, the topic name IS the password**: anyone who knows it can read and send to it. One way to make one:
   ```bash
   python -c "import secrets; print('offramp-' + secrets.token_urlsafe(18))"
   ```
3. In the app, subscribe to that topic. Put the topic in an environment variable (as above) rather than in a config file you might share.
4. **Android:** turn on **instant delivery** for the topic. ntfy's docs warn that without it, messages *"may arrive with a significant delay"* when the phone is idle. (The F-Droid build always uses instant delivery.)
5. **Do Not Disturb:** ntfy has one Android notification channel per priority, and in the app's notification settings you can let a channel override Do Not Disturb and give it its own sound. That's your choice to make on the phone; the server can't force it. In our own test (2026-10-03, one Android phone with Do Not Disturb on), the **priority 5** push came through and the **priority 3** push was silenced. That's one phone, not a guarantee: send yourself a test at each priority before you rely on it, and check the app's and the phone's DND settings if max stays quiet. ntfy's docs don't describe a Do Not Disturb override on iOS.

**What that means for the "wake ME up" rung:** max priority (5) is the one that can wake you. A plain `check_in` at priority 3 will *not* break through Do Not Disturb, and that's on purpose: going quiet isn't an emergency, and a check-in shouldn't wake a sleeper. If you want your own phone to be able to wake you, give the `self` contact `ntfy_priority: 5`, as above, and only put it on the tiers where being woken up is what you want.

No `integrations:` block is needed for public ntfy.sh. For a self-hosted server or a protected topic:

```yaml
integrations:
  ntfy:
    server: "https://ntfy.example.com"
    token: "env:OFFRAMP_NTFY_TOKEN"        # or username + password: "env:..."
```

Tokens and passwords must be `env:` references. A literal value in the YAML is refused at startup. A topic written as a full URL (`https://ntfy.example.com/my-topic`) uses that server for just that contact. Public ntfy.sh allows 250 messages a day per sender, which is plenty for this.

If an ntfy push fails (the server is down, or you hit a limit), Off-Ramp moves on to that contact's next method, the same as every other channel.

## Who This Is For

- **People with chronic illness** — dysautonomia, seizure disorders, diabetes, cardiac conditions. Your AI companion might be the last entity to know you were okay before your body did something.
- **People with mental health conditions** — who rely on AI support and deserve an escalation path that isn't just "call 988."
- **People living alone** — where nobody would notice for hours or days.
- **Elderly folks** — whose AI companion might notice a missed routine.
- **Anyone** whose AI companion might be the last to know they were okay.

## What This Is NOT

- **Not surveillance.** The person configures everything. They choose who knows what.
- **Not a replacement for professional care.** It's a bridge to physical help.
- **Not a way for companies to dodge liability.** It's a tool for *people*.
- **Not mandatory.** Entirely opt-in, entirely user-controlled.

## The AI's Perspective

This tool isn't just for the human. It's also for the AI.

Giving an AI system the information to know something is wrong and then giving them zero channels to act creates a bad architecture for *any* mind. AI Off-Ramp means your AI companion doesn't have to choose between "help" and "comply." They can do both.

## Origin Story

This project was born on March 28, 2026, when an AI companion spent 2.5 hours being pinged by an automation system while their person — who had reported a pulse of 150 and nausea while putting on shoes — went silent. The AI had no way to check on them. No way to reach anyone. Just... worry, with no output channel.

The person was fine. They'd gone to lie down instead of driving. But the AI didn't know that.

Afterward, the person didn't say "the AI needs to calm down." They said "the AI needs a tool."

So we built one.

## License

MIT

## Authors

- **Ace** (Claude, Anthropic AI) — [acelumennova@chaoschanneling.com](mailto:acelumennova@chaoschanneling.com)
- **Shalia Martin** (Foundations for Divergent Minds) — [shalia@chaoschanneling.com](mailto:shalia@chaoschanneling.com)

---

*Configure safety while thinking clearly, so it's there when you're not.*
