# scout-bot

Telegram bot ("Scout") that acts as a personal sourcing agent: describe what you want to buy or book (text or photo) and it returns 3–5 real options with prices and links, using the Anthropic API with web search. Runs live on a Linux server (`scout-bot.service`).

## Stack & layout
- Python; deps in `requirements.txt`.
- `bot.py` — Telegram interface (python-telegram-bot, polling). Agent-agnostic: it calls `agents.handle_request()` and renders the returned `AgentResponse`. Handles commands, photos (download → base64), and optional access control.
- `agents/` — the agent layer. `route()` picks an agent; `ScoutAgent` calls the Anthropic API with the `web_search` tool. Anthropic calls run via `asyncio.to_thread` so polling stays responsive.
- Deployment: `DEPLOY.md`, `deploy/`, and `scout-bot.service` (systemd unit).

## Constraints
- Keep the `bot.py` / `agents/` boundary clean: Telegram concerns stay in `bot.py`, model/agent logic stays in `agents/`. New capabilities are new agents behind `route()`, not branches in the bot — the bot is deliberately agent-agnostic.
- Anthropic/model changes: check the current model IDs and web_search tool docs rather than assuming from memory; they change faster than training data.
- Never commit tokens or API keys; they come from the environment/service unit.

## Verify a change
- Run `bot.py` locally with a test bot token and send a real request end-to-end (including a photo, if the change touches images) before any deploy.
