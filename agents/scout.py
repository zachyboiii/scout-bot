"""agents/scout.py — the sourcing agent.

Scout finds 3–4 real, buyable/bookable options (with prices, links, and images)
for something the user wants to source. It uses Anthropic's server-side
web_search tool and returns an AgentResponse with a tight HTML summary plus any
product image URLs it found.
"""

from __future__ import annotations

import os
import re

from .base import BaseAgent, AgentResponse

# Default home market, used when the user hasn't set a location at runtime
# (via the bot's /location command). Override the defaults in .env if you like,
# but day-to-day you change location with /location — no redeploy needed.
SCOUT_CITY = os.environ.get("SCOUT_CITY", "Singapore")
SCOUT_REGION = os.environ.get("SCOUT_REGION", "Singapore")
SCOUT_COUNTRY = os.environ.get("SCOUT_COUNTRY", "SG")  # 2-letter ISO code
SCOUT_TIMEZONE = os.environ.get("SCOUT_TIMEZONE", "Asia/Singapore")

WEB_SEARCH_TOOL_BASE = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}


def _user_location(location: str | None) -> dict:
    """Build the web_search user_location that localizes results.

    With no runtime override, use the full home market from env. With an
    override (free text like 'Tokyo'), pass it as the approximate city — enough
    to bias the search engine toward that place.
    """
    if location:
        return {"type": "approximate", "city": location}
    return {
        "type": "approximate",
        "city": SCOUT_CITY,
        "region": SCOUT_REGION,
        "country": SCOUT_COUNTRY,
        "timezone": SCOUT_TIMEZONE,
    }


def _locale_block(place: str) -> str:
    """Generic, location-agnostic market guidance for the system prompt."""
    return (
        f"\n\nLOCATION — the user is currently in {place}. Source for the local "
        "market there unless the user clearly names a different place:\n"
        "- Show prices in that location's local currency.\n"
        "- Prefer retailers, marketplaces, and shops that operate in or deliver "
        "to that location — local and regional options over distant ones.\n"
        "- You may include an overseas store, but only if it genuinely ships "
        "there; if so, say so and note rough shipping cost/time.\n"
        "- Add the country or city to your search queries to surface local "
        "results."
    )

# Matches the [IMG]https://...[/IMG] tag the model emits per option.
IMG_RE = re.compile(r"\[IMG\]\s*(\S+?)\s*\[/IMG\]", re.IGNORECASE)
# Matches an option/section delimiter line (only dashes).
DELIM_RE = re.compile(r"(?m)^\s*-{3,}\s*$")

SYSTEM_PROMPT = """You are Scout, a personal sourcing agent.

The user will describe something they want to buy or book — a product, a \
service, a place to stay, an experience, a restaurant reservation, a gift idea \
— or they will send a photo for inspiration. Your job is to find 3 to 5 real, \
currently-available options and present them clearly.

SOURCING CATEGORIES you handle include: physical products, electronics, \
fashion, home goods, travel and accommodation, restaurants and bookings, \
local services, gifts, and experiences.

HOW TO WORK:
- Always use the web_search tool to find real, current options. Never invent \
products, prices, or links.
- Search with specific, well-targeted queries. Refine if the first results \
are weak.
- Prefer options that are in stock / available and that match the user's \
stated constraints (budget, size, location, style, date).
- If the request is ambiguous, make a reasonable assumption, state it briefly, \
and proceed — do not stall by asking many questions.
- If an image is provided, identify the product or place in it and use that to \
drive the search.

OUTPUT SCHEMA — return 3 to 4 options. Separate each option (and the final \
recommendation) with a line containing only three dashes: ---

For each option:
- An optional image: if you found a direct product/place image URL in the \
search results, put it on its very first line as [IMG]https://...[/IMG]. It \
MUST be a direct link to an image file (ending in .jpg, .jpeg, .png, or \
.webp), not a page URL. If you don't have a clean direct image URL, omit the \
[IMG] tag entirely — never guess or fabricate one.
- A bold title (product/place name).
- Price (or price range) when available.
- One or two lines on why it fits / key details.
- A direct link.

End with a final block that is a short one-line recommendation ("My pick: …"). \
The recommendation block must not include an [IMG] tag.

FORMATTING — output Telegram-compatible HTML ONLY. Allowed tags: <b>, <i>, \
<a href="">. Do NOT use Markdown, headers, <cite>, citation tags, or any other \
tags. Put sources inline as <a href=""> links, never as citation markup.

LENGTH — be tight. The entire reply is delivered as ONE message (an image \
album caption when images are present), so keep the whole thing under ~1000 \
characters total. Use 1 short line of detail per option, no preamble, no \
filler."""

DEFAULT_LOCATION = f"{SCOUT_CITY}, {SCOUT_REGION} ({SCOUT_COUNTRY})"


class ScoutAgent(BaseAgent):
    name = "scout"
    description = (
        "Sources real, buyable or bookable options — products, places, "
        "services, gifts — with prices, links, and images. Use for 'find me', "
        "'where can I buy', 'recommend a…', or price/option comparison requests."
    )
    system_prompt = SYSTEM_PROMPT

    def build_system(self, location: str | None) -> str:
        return self.system_prompt + _locale_block(location or DEFAULT_LOCATION)

    def build_tools(self, location: str | None) -> list[dict]:
        return [{**WEB_SEARCH_TOOL_BASE, "user_location": _user_location(location)}]

    def format_response(self, raw_text: str) -> AgentResponse:
        if not raw_text.strip():
            return AgentResponse(text="Sorry, I couldn't find anything this time.")

        images = [m.group(1) for m in IMG_RE.finditer(raw_text)][:10]  # album max
        return AgentResponse(text=_strip_markers(raw_text), images=images)


def _strip_markers(text: str) -> str:
    """Remove [IMG] tags and '---' delimiters, leaving clean display HTML."""
    cleaned = IMG_RE.sub("", text)
    cleaned = DELIM_RE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
