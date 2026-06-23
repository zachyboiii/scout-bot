"""agents — agent registry and router.

This package decouples the bot from any specific agent. The bot calls
`handle_request(...)` and gets back an AgentResponse; it never needs to know
which agent ran.

Adding a new agent (e.g. a Planner) is two steps:
  1. Create agents/planner.py with `class PlannerAgent(BaseAgent)`.
  2. Add `PlannerAgent()` to the `_AGENTS` tuple below.
`route()` is then the single place to teach the system when to pick it.
"""

from __future__ import annotations

from .base import AgentResponse, BaseAgent, InsufficientCreditsError
from .scout import ScoutAgent

# Instantiate the available agents once (they're stateless and reusable).
_scout = ScoutAgent()

# Register every agent here. Planner gets added to this tuple later.
_AGENTS: tuple[BaseAgent, ...] = (_scout,)
REGISTRY: dict[str, BaseAgent] = {a.name: a for a in _AGENTS}

DEFAULT_AGENT = _scout


def route(
    text: str,
    history: list[dict] | None = None,
    image_b64: str | None = None,
) -> BaseAgent:
    """Pick the agent best suited to a request.

    Only Scout exists today, so everything routes to it. This is the single
    place to add intent classification when the planner lands — a keyword
    heuristic or a small LLM classifier choosing from REGISTRY by each agent's
    `.description`. Agents can also reach into REGISTRY to delegate to one
    another (e.g. a planner asking Scout to source venues), which is how
    multi-agent workflows are composed.
    """
    return DEFAULT_AGENT


def handle_request(
    text: str,
    history: list[dict] | None = None,
    image_b64: str | None = None,
    media_type: str | None = None,
    location: str | None = None,
) -> AgentResponse:
    """Route the request to an agent and run it. Synchronous/blocking."""
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
