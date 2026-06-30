"""agents — agent registry and router.

Three modes of operation:
  scout_only   — user wants specific recommendations/products/places.
  plan_only    — user wants a general activity plan (no specific venues).
  itinerary    — user wants a detailed itinerary; Planner + Scout run in
                 parallel. Planner builds the structure; Scout finds real
                 venues for each leg. Results are merged into one response.

Adding a new agent is two steps:
  1. Create agents/<name>.py with `class XAgent(BaseAgent)`.
  2. Add `XAgent()` to the `_AGENTS` tuple below.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import AgentResponse, BaseAgent, InsufficientCreditsError
from .planner import PlannerAgent
from .scout import ScoutAgent

_scout = ScoutAgent()
_planner = PlannerAgent()

_AGENTS: tuple[BaseAgent, ...] = (_scout, _planner)
REGISTRY: dict[str, BaseAgent] = {a.name: a for a in _AGENTS}

DEFAULT_AGENT = _scout

# ---------------------------------------------------------------------------
# Intent classification — keyword-based, no LLM call (fast, zero cost).
#
# Priority order (highest to lowest): itinerary → plan_only → scout_only.
# Each tier has its own regex. A hit in a higher tier short-circuits the rest.
# ---------------------------------------------------------------------------

# ITINERARY — user wants a detailed, time-sequenced plan WITH real venue picks.
# Covers: explicit itinerary words, "plan a day/trip/outing/holiday", multi-day
# structures, "walk me through", "how should I spend", time-structured asks,
# destination-framed planning ("trip to X", "visiting X for N days"), and
# casual equivalents ("map out my day", "what's the move for Saturday").
_ITINERARY_KW = re.compile(
    r"\b("
    # Explicit itinerary / schedule words
    r"itinerary|itin\b|day\s*plan|day\s*by\s*day|hour\s*by\s*hour|"
    r"schedule\s*(for|a\b|my\b|the\b|out\b)|walk\s*me\s*through|map\s*(out|my)|"
    r"lay\s*(out|it\s*out)|breakdown|break\s*it\s*down|"
    # Explicit trip/outing planning phrases
    r"plan\s*(a\b|an\b|my\b|our\b|the\b)?\s*"
    r"(day\b|trip\b|outing\b|holiday\b|vacation\b|getaway\b|weekend\b|"
    r"morning\b|afternoon\b|evening\b|night\b|visit\b|excursion\b|tour\b|"
    r"staycation\b)|"
    r"(trip|travel|outing|holiday|vacation|weekend|getaway)\s*plan\b|"
    r"(full|whole|entire)\s*(day|trip|weekend)\b|"
    # Duration-framed travel ("X days in Y", "spending N days")
    r"\d+[\-\s]?day\s*(trip|itinerary|plan|in\b|at\b)|"
    r"spending\s*\d+\s*(day|night|week)s?\s*(in\b|at\b)|"
    r"(visiting|going\s*to|travelling\s*to|traveling\s*to)\s*\w+\s*for\s*\d+\s*(day|night)s?\b|"
    # Casual / colloquial planning
    r"what('s|\s*is)\s*the\s*(move|plan|vibe)\s*(for\b|on\b|this\b|saturday\b|sunday\b|"
    r"tonight\b|tomorrow\b|the\s*weekend\b)|"
    r"how\s*(do|should|can|could)\s*(i|we)\s*spend\s*(the|a|my|our)?\s*"
    r"(day\b|morning\b|afternoon\b|evening\b|night\b|weekend\b|trip\b)|"
    r"(plan|organise|organize)\s*(the|my|our)\s*(day\b|trip\b|outing\b|weekend\b|night\b)|"
    # Multi-stop / route planning
    r"(route|stops?)\s*(for\b|through\b|around\b)|multi[\-\s]?(stop|day|city)|"
    r"where\s*(should|can|do)\s*(i|we)\s*(go|head|start)\s*(first\b|next\b|after\b)|"
    r"(sequence|order)\s*(of\b|for\b)?\s*(stops?|places?|activities?|things?)"
    r")",
    re.IGNORECASE,
)

# PLAN_ONLY — user wants activity/outing ideas or a loose plan without needing
# specific venue links. Covers: "things to do", "what can we do", "any ideas",
# "suggest activities", lazy weekend suggestions, and similar exploratory asks
# that don't need Scout's product/booking search.
_PLAN_KW = re.compile(
    r"\b("
    # What to do / activity exploration
    r"(what|where)\s*(should|can|could|would)\s*(i|we|you)\s*(do|go|see|try|eat|visit)\b|"
    r"things?\s*to\s*(do|see|try|visit|explore|eat)\b|"
    r"(fun|cool|good|great|nice|interesting)\s*things?\s*to\s*(do|try|see)\b|"
    r"activities?\s*(to\b|for\b|ideas?\b|suggestion|around\b|in\b|near\b)|"
    r"what('s|\s*is|s\b)?\s*(there|on|happening|good)\s*(to\s*do\b|around\b|nearby\b|tonight\b|"
    r"this\s*weekend\b|in\b)?"
    r"|"
    # Idea / suggestion requests
    r"(any\b|some\b|got\s*(any\b|some\b))?\s*ideas?\s*(for\b|on\b|about\b)|"
    r"suggest\s*(something|some|activities?|things?|places?|options?)|"
    r"(recommend|give\s*me)\s*(some\b|a\b|an\b)?\s*(ideas?|suggestions?|activities?|options?)\b|"
    r"any\s*(recommendations?\b|suggestions?\b|ideas?\b|tips?\b)\s*(for\b|on\b)?"
    r"|"
    # Loose planning / how to spend time
    r"how\s*(can|could|do)\s*(i|we)\s*(spend|fill|enjoy|make\s*the\s*most\s*of)\b|"
    r"(best\s*way|good\s*ways?)\s*to\s*(spend|enjoy|use|fill)\s*(a\b|an\b|the\b|my\b)?\s*"
    r"(day\b|morning\b|afternoon\b|evening\b|night\b|weekend\b|hour\b)|"
    r"(looking\s*for\s*(something|things?|ideas?|ways?)\s*to\s*do)|"
    r"(kill|fill)\s*(some\b|a\s*few\b)?\s*(time\b|hours?\b)|"
    # Casual / weekend exploration
    r"(lazy|free|long)\s*(day\b|morning\b|afternoon\b|evening\b|weekend\b)|"
    r"(bored|nothing\s*to\s*do)\b|"
    r"(explore|discover)\s*(the\b|a\b)?\s*(area|neighbourhood|neighborhood|city|town|place)\b|"
    r"(date\s*night|date\s*idea|family\s*(outing|day|activity)|group\s*(activity|outing))\b|"
    r"(kid[\-\s]?friendly|family[\-\s]?friendly)\s*(activities?|things?|ideas?|options?)\b"
    r")",
    re.IGNORECASE,
)

# SCOUT_ONLY — user wants to find, buy, book, or compare specific
# products/venues/services. Covers: shopping, bookings, price checks,
# "best X near me", restaurant/hotel/product searches, and availability checks.
_SCOUT_KW = re.compile(
    r"\b("
    # Core find/search/look verbs
    r"find\s*(me\b|a\b|an\b|some\b|the\b)?|search\s*(for\b)?|look\s*(for\b|up\b)|"
    r"(show|give)\s*me\s*(some\b|a\b|options?\b|recommendations?\b)|"
    r"hunt\s*(for\b|down\b)|source\s*(me\b|a\b|some\b)?|track\s*down\b|"
    # Commerce / booking verbs
    r"buy|purchase|order|get\s*me\b|shop\s*(for\b)?|"
    r"book(ing)?\b|reserve|reservation|hire|rent(al)?\b|"
    r"(check\s*(availability|if\s*available|if\s*open))|"
    # Recommendation requests for specific things
    r"recommend\s*(a\b|an\b|me\b|some\b|the\s*best\b)?|"
    r"suggest\s*(a\b|an\b|me\b|the\s*best\b)?|"
    r"best\s+(place|spot|restaurant|cafe|coffee|bar|club|hotel|hostel|airbnb|"
    r"shop|store|mall|market|gym|spa|salon|clinic|service|option|deal)\b|"
    r"(top|good|great|cheap|affordable|budget|luxury|fancy|hidden\s*gem)\s*"
    r"(restaurant|cafe|bar|hotel|hostel|shop|store|place|spot)\b|"
    # Price / availability signals
    r"(how\s*much|what('s|\s*is|s\b)?\s*the\s*(price|cost|rate|fee|charge))|"
    r"price\b|cost\b|rates?\b|deals?\b|offers?\b|discount\b|promo\b|sale\b|"
    r"(is\s*(it\b)?\s*(available|in\s*stock|open))|available\b|in\s*stock\b|"
    # Near me / location-based product search
    r"near\s*(me|here|by)\b|nearby\b|close\s*by\b|around\s*here\b|in\s*the\s*area\b|"
    r"(where\s*(can|do)\s*(i|we)\s*(buy|get|find|order|try|eat|drink|go\s*for))\b|"
    r"(where\s*to\s*(buy|get|find|eat|drink|stay|book|rent|hire))\b|"
    # Specific product/service category lookups
    r"(restaurant|cafe|coffee\s*shop|bar|pub|club|hotel|hostel|airbnb|resort|spa|"
    r"gym|salon|barbershop|clinic|pharmacy|supermarket|grocery|market|mall|"
    r"electronics?|laptop|phone|camera|headphones?|watch|shoes?|bag|clothes?|"
    r"dress|jacket|sneakers?|fashion|furniture|appliance|gift\b|present\b)\b|"
    # Explicit options/alternatives
    r"options?\s*(for\b|to\b)?|alternatives?\s*(to\b|for\b)?|"
    r"compare\b|vs\b|versus\b|which\s*(is\s*)?(better|best|cheaper|nicer)\b"
    r")",
    re.IGNORECASE,
)


def _classify(text: str) -> str:
    """Return 'itinerary', 'plan_only', or 'scout_only'.

    Priority: itinerary > plan_only > scout_only.
    An itinerary hit always wins, even if scout keywords are present.
    plan_only only wins if no scout keywords appear alongside it.
    """
    if _ITINERARY_KW.search(text):
        return "itinerary"
    plan = bool(_PLAN_KW.search(text))
    scout = bool(_SCOUT_KW.search(text))
    if plan and not scout:
        return "plan_only"
    return "scout_only"


def route(
    text: str,
    history: list[dict] | None = None,
    image_b64: str | None = None,
) -> BaseAgent:
    """Return the single agent for non-itinerary requests.

    For itinerary requests (both agents), call handle_request() directly —
    it handles the parallel case itself.
    """
    if image_b64:
        return _scout  # Images always go to Scout.
    intent = _classify(text)
    if intent == "plan_only":
        return _planner
    return _scout  # scout_only or fallback


# ---------------------------------------------------------------------------
# Parallel itinerary execution
# ---------------------------------------------------------------------------

def _run_itinerary(
    text: str,
    history: list[dict] | None,
    location: str | None,
) -> AgentResponse:
    """Run Planner and Scout in parallel and merge the results.

    Scout is given the original user request so it finds venues that fit.
    Both run simultaneously; whichever finishes last determines total latency
    (usually similar since both are single Haiku calls without web search for
    Planner, and one web search round-trip for Scout).
    """
    planner_resp: AgentResponse | None = None
    scout_resp: AgentResponse | None = None

    def run_planner():
        return _planner.run(text, history=history, location=location)

    def run_scout():
        # Ask Scout to find real venues that fit this itinerary request.
        scout_prompt = (
            f"Find real places to visit for this itinerary request: {text}. "
            "For each part of the day (morning, afternoon, evening), find 1–2 "
            "real venues with links and brief details. Group them by time of day."
        )
        return _scout.run(scout_prompt, history=None, location=location)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(run_planner): "planner",
            pool.submit(run_scout): "scout",
        }
        for future in as_completed(futures):
            label = futures[future]
            result = future.result()  # re-raises any exception from the thread
            if label == "planner":
                planner_resp = result
            else:
                scout_resp = result

    # Merge: plan structure first, then venue picks below it.
    parts: list[str] = []
    if planner_resp and planner_resp.text:
        parts.append(planner_resp.text)
    if scout_resp and scout_resp.text:
        parts.append("<b>Venue picks</b>")
        parts.append(scout_resp.text)

    merged_text = "\n\n".join(parts)
    merged_images = (scout_resp.images if scout_resp else [])[:10]
    return AgentResponse(text=merged_text, images=merged_images)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def handle_request(
    text: str,
    history: list[dict] | None = None,
    image_b64: str | None = None,
    media_type: str | None = None,
    location: str | None = None,
) -> AgentResponse:
    """Route the request and run the appropriate agent(s). Synchronous/blocking."""
    if not image_b64 and _classify(text) == "itinerary":
        return _run_itinerary(text, history, location)

    agent = route(text, history, image_b64)
    return agent.run(
        text,
        history=history,
        image_b64=image_b64,
        media_type=media_type,
        location=location,
    )


__all__ = [
    "AgentResponse",
    "BaseAgent",
    "InsufficientCreditsError",
    "REGISTRY",
    "DEFAULT_AGENT",
    "route",
    "handle_request",
]
