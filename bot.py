"""bot.py — Telegram interface layer for Scout.

Built on python-telegram-bot v21 (async). Handles commands and messages,
downloads images, bridges to the synchronous agent via a thread, and splits
long replies to respect Telegram's 4096-character message limit.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re

import httpx
from dotenv import load_dotenv
from telegram import InputMediaPhoto, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agents import AgentResponse, InsufficientCreditsError, handle_request

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("scout")

TELEGRAM_MAX = 4096
TELEGRAM_CAPTION_MAX = 1024
HISTORY_LIMIT = 20  # 10 exchanges

# Tags Telegram's HTML parse mode actually accepts. Anything else (e.g. the
# <cite> tags the model adds for web-search citations) must be stripped or the
# whole message is rejected with a BadRequest.
ALLOWED_TG_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "a", "code", "pre", "blockquote", "tg-spoiler",
}
_TAG_RE = re.compile(r"</?([a-zA-Z0-9-]+)(?:\s[^>]*)?>")


def _sanitize_html(text: str) -> str:
    """Drop any tag Telegram doesn't support, keeping the inner text."""
    return _TAG_RE.sub(
        lambda m: m.group(0) if m.group(1).lower() in ALLOWED_TG_TAGS else "",
        text,
    )


def _strip_tags(text: str) -> str:
    """Remove all tags for a plain-text fallback send."""
    return _TAG_RE.sub("", text)

WELCOME = (
    "👋 <b>Hi, I'm Scout</b> — your personal sourcing agent.\n\n"
    "Tell me what you want to buy or book, and I'll find 3–5 real options with "
    "prices and links. You can also send a photo for inspiration.\n\n"
    "<i>e.g. \"a quiet 3-night stay near Kyoto under $200/night\" or "
    "\"wireless noise-cancelling headphones under $150\"</i>\n\n"
    "Commands: /help · /location · /clear"
)

HELP = (
    "<b>How to use Scout</b>\n\n"
    "• Send a message describing what you want — include budget, size, "
    "location, or dates for better results.\n"
    "• Send a photo (with an optional caption) to source something similar.\n"
    "• I source for your current location — set it with /location when you "
    "travel.\n\n"
    "<b>Commands</b>\n"
    "/start — intro\n"
    "/help — this message\n"
    "/location &lt;place&gt; — set where to source for (e.g. /location Tokyo)\n"
    "/clear — wipe our conversation history"
)


def _parse_allowlist() -> tuple[set[int], set[str]]:
    """Parse ALLOWED_USERS into numeric IDs and lowercased usernames.

    Each comma-separated entry may be a numeric Telegram user ID (e.g.
    123456789) or a username (with or without a leading @).
    """
    raw = os.environ.get("ALLOWED_USERS", os.environ.get("ALLOWED_USER_IDS", "")).strip()
    ids: set[int] = set()
    usernames: set[str] = set()
    for part in raw.split(","):
        part = part.strip().lstrip("@")
        if not part:
            continue
        if part.isdigit():
            ids.add(int(part))
        else:
            usernames.add(part.lower())
    return ids, usernames


ALLOWED_IDS, ALLOWED_USERNAMES = _parse_allowlist()


def _is_allowed(user) -> bool:
    if not ALLOWED_IDS and not ALLOWED_USERNAMES:
        return True  # no allowlist set → public
    if user.id in ALLOWED_IDS:
        return True
    if user.username and user.username.lower() in ALLOWED_USERNAMES:
        return True
    return False


def _split_message(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Split text into <=limit chunks, preferring paragraph then line breaks."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at == -1:
            split_at = window.rfind("\n")
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def _reply_html(message, text: str) -> None:
    """Reply with sanitized HTML; fall back to plain text if Telegram rejects it."""
    try:
        await message.reply_text(
            _sanitize_html(text),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest:
        logger.warning("HTML parse failed; resending as plain text")
        await message.reply_text(_strip_tags(text), disable_web_page_preview=True)


async def _send_text(message, text: str) -> None:
    """Send (possibly long) HTML text, split to respect Telegram's limit."""
    for chunk in _split_message(text):
        await _reply_html(message, chunk)


async def _deliver(message, response: AgentResponse) -> None:
    """Deliver an agent's response as a single message.

    The summary goes out as one text body. If the agent produced images, they're
    sent together as one photo album (a single grouped message in Telegram),
    with the summary as the album caption when it fits. A bad image URL or any
    send failure degrades gracefully to a plain text message — the findings are
    never lost.
    """
    full_text = response.text
    images = response.images[:10]  # album max is 10

    if not images:
        await _send_text(message, full_text)
        return

    caption = _sanitize_html(full_text)
    caption_fits = len(caption) <= TELEGRAM_CAPTION_MAX

    media = []
    for i, url in enumerate(images):
        if i == 0 and caption_fits:
            media.append(InputMediaPhoto(url, caption=caption, parse_mode=ParseMode.HTML))
        else:
            media.append(InputMediaPhoto(url))

    try:
        await message.reply_media_group(media)
        if not caption_fits:
            # Summary too long for an album caption — send it as its own message.
            await _send_text(message, full_text)
    except Exception:  # noqa: BLE001 — bad image URL etc.; never drop the findings
        logger.warning("Album send failed; falling back to a single text message")
        await _send_text(message, full_text)


# --- Command handlers -------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user):
        return
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user):
        return
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user):
        return
    context.user_data.pop("history", None)
    await update.message.reply_text("🧹 Conversation history cleared.")


async def location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set/show the user's current location used to localize sourcing.

    /location Tokyo     → set it
    /location           → show current
    /location reset     → back to the default (home) market
    """
    if not _is_allowed(update.effective_user):
        return

    arg = " ".join(context.args).strip()

    if not arg:
        current = context.user_data.get("location")
        if current:
            await update.message.reply_text(
                f"📍 Sourcing for <b>{current}</b>.\n"
                "Change it with <code>/location &lt;place&gt;</code>, or "
                "<code>/location reset</code> for your default.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "📍 Using your default location.\n"
                "Set a different one with <code>/location &lt;place&gt;</code> "
                "(e.g. <code>/location Tokyo</code>).",
                parse_mode=ParseMode.HTML,
            )
        return

    if arg.lower() in {"reset", "default", "clear", "home"}:
        context.user_data.pop("location", None)
        await update.message.reply_text("📍 Location reset to your default.")
        return

    context.user_data["location"] = arg
    await update.message.reply_text(
        f"📍 Got it — now sourcing for <b>{arg}</b>.", parse_mode=ParseMode.HTML
    )


# --- Message handler --------------------------------------------------------


async def _download_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return (base64_str, media_type) for an attached photo/image, or (None, None)."""
    file_id = None
    media_type = "image/jpeg"

    if update.message.photo:
        file_id = update.message.photo[-1].file_id  # highest resolution
    elif update.message.document and (update.message.document.mime_type or "").startswith("image/"):
        file_id = update.message.document.file_id
        media_type = update.message.document.mime_type

    if not file_id:
        return None, None

    tg_file = await context.bot.get_file(file_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(tg_file.file_path)
        resp.raise_for_status()
        data = base64.standard_b64encode(resp.content).decode("ascii")
    return data, media_type


def _is_group_chat(update: Update) -> bool:
    return update.effective_chat.type in ("group", "supergroup")


def _is_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if the bot's @username appears in the message text/caption."""
    bot_username = context.bot.username
    if not bot_username:
        return False
    text = update.message.text or update.message.caption or ""
    return f"@{bot_username}".lower() in text.lower()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(user):
        return

    if _is_group_chat(update) and not _is_mentioned(update, context):
        return

    # Strip the @mention from the text before passing to the agent.
    raw_text = update.message.text or update.message.caption or ""
    bot_username = context.bot.username
    if bot_username:
        text = re.sub(rf"@{re.escape(bot_username)}", "", raw_text, flags=re.IGNORECASE).strip()
    else:
        text = raw_text
    image_b64, media_type = await _download_image(update, context)

    if not text and not image_b64:
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status = await update.message.reply_text("🔎 Searching…")

    history: list[dict] = context.user_data.get("history", [])

    try:
        response = await asyncio.to_thread(
            handle_request,
            text,
            history,
            image_b64,
            media_type,
            context.user_data.get("location"),
        )
    except InsufficientCreditsError:
        logger.warning("Anthropic API out of credits")
        await status.edit_text(
            "💳 Scout is out of API credits right now, so I can't search.\n\n"
            "The bot owner needs to top up at console.anthropic.com → Billing. "
            "Please try again once that's done."
        )
        return
    except Exception:  # noqa: BLE001 — surface a friendly error, log the detail
        logger.exception("handle_request failed for user %s", user.id)
        await status.edit_text("⚠️ Something went wrong while searching. Please try again.")
        return

    await status.delete()

    await _deliver(update.message, response)

    # Persist text-only history (images noted, not stored).
    user_note = f"[Image + caption]: {text}" if image_b64 else text
    history = history + [
        {"role": "user", "content": user_note},
        {"role": "assistant", "content": response.text},
    ]
    context.user_data["history"] = history[-HISTORY_LIMIT:]


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any uncaught handler error and let the user know something failed."""
    logger.error("Unhandled error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong handling that. Please try again."
            )
        except Exception:  # noqa: BLE001 — never let the error handler itself raise
            pass


# --- Entry point ------------------------------------------------------------


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("location", location))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.IMAGE,
            handle_message,
        )
    )
    app.add_error_handler(on_error)

    if ALLOWED_IDS or ALLOWED_USERNAMES:
        logger.info(
            "Access restricted — IDs: %s, usernames: %s",
            sorted(ALLOWED_IDS),
            sorted(ALLOWED_USERNAMES),
        )
    else:
        logger.info("No allowlist set — bot is public.")

    logger.info("Scout is polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
