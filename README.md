# Scout

A Telegram bot that acts as a personal sourcing agent. Describe what you want to
buy or book (or send a photo), and Scout returns 3–5 real options with prices,
links, and key details — powered by the Anthropic API with live web search.

## How it works

```
Telegram ──polling──► bot.py ──asyncio.to_thread──► agents.handle_request()
                                                          │
                                                    route() picks an agent
                                                          │
                                                    ScoutAgent ──► Anthropic API
                                                                   + web_search
```

- **`bot.py`** — Telegram interface (python-telegram-bot). Commands,
  message/photo handling, image download → base64, single-message delivery,
  optional access control. It is agent-agnostic: it calls
  `agents.handle_request()` and renders the returned `AgentResponse`.
- **`agents/`** — the agent layer (see below).

### The `agents/` package

The bot never talks to a specific agent directly — it goes through a router, so
new agents (e.g. a future **Planner** for outings/itineraries) slot in without
touching `bot.py`.

| File | Role |
|---|---|
| `agents/base.py` | `BaseAgent` (shared agentic loop, model/tool config, image input) and the `AgentResponse` the bot renders. |
| `agents/scout.py` | `ScoutAgent` — sourcing agent using the `web_search_20250305` tool. |
| `agents/__init__.py` | Registry + `route()` (which agent handles a request) + `handle_request()`. |

**Adding an agent** (e.g. Planner):
1. Create `agents/planner.py` with `class PlannerAgent(BaseAgent)`.
2. Add `PlannerAgent()` to the `_AGENTS` tuple in `agents/__init__.py`.
3. Teach `route()` when to pick it. Agents can also reach into `REGISTRY` to
   delegate to one another — e.g. a planner asking Scout to source venues —
   which is how multi-agent workflows are composed.

### Images

- **Reference in:** send a photo (with optional caption) and Scout identifies
  the product/place in it to drive the search.
- **Images out:** when the web search turns up clean direct image URLs, the bot
  sends them as a single photo album with the summary as the caption. If an
  image URL is missing or unreachable, it degrades to a text-only message — so a
  bad link never drops the result.

Conversation history is kept in-memory per user (last 10 exchanges) and resets
when the process restarts.

## Setup (local)

Requires Python 3.11+.

```bash
python -m venv venv
# Windows:  venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in your tokens
python bot.py
```

Get a `TELEGRAM_BOT_TOKEN` from [@BotFather](https://t.me/BotFather) and an
`ANTHROPIC_API_KEY` from [console.anthropic.com](https://console.anthropic.com).

To restrict access, set `ALLOWED_USER_IDS` to a comma-separated list of Telegram
user IDs (message [@userinfobot](https://t.me/userinfobot) to find yours).

## Commands

| Command | Description |
|---|---|
| `/start` | Intro message |
| `/help`  | Usage tips |
| `/location <place>` | Set the market to source for (e.g. `/location Tokyo`). No arg shows the current one; `/location reset` returns to the default. |
| `/clear` | Wipe your conversation history |

### Localization

Scout localizes results to your location so you get local shops and currency
instead of US defaults. It uses the `web_search` tool's `user_location` plus a
prompt instruction. The **default** market comes from the `SCOUT_*` env vars
(Singapore out of the box); when you travel, set your current place at runtime
with **`/location`** — it takes effect immediately, no redeploy. The override is
per-user and resets on bot restart.

## Deploy (systemd on Ubuntu)

```bash
# On the VM, as the ubuntu user:
git clone <repo> /home/ubuntu/scout-bot && cd /home/ubuntu/scout-bot
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in secrets

sudo cp scout-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scout-bot
journalctl -u scout-bot -f   # follow logs
```

The bot uses outbound polling only — no inbound ports needed.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `ANTHROPIC_API_KEY`  | ✅ | Key from console.anthropic.com |
| `ALLOWED_USER_IDS`   | ❌ | Comma-separated Telegram user IDs. Empty = public |
