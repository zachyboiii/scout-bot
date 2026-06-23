"""agents/base.py — shared foundation for all Scout agents.

A concrete agent (Scout today, Planner later) declares its model, system
prompt, and tools, then implements `format_response()` to turn the model's
final text into an AgentResponse. The synchronous agentic loop is shared here
so every agent gets the same web-search / tool-use behaviour for free.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import anthropic
from anthropic import Anthropic

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class InsufficientCreditsError(Exception):
    """Raised when the Anthropic API rejects a request for lack of credits.

    The bot catches this to show a specific 'out of credits' message instead of
    the generic error.
    """


def _is_credit_error(exc: anthropic.APIStatusError) -> bool:
    """True if an API error is about an empty/low credit balance.

    Primary signal is the error's `.type` ('billing_error'); we also match the
    known message text as a fallback in case the type string shifts.
    """
    if getattr(exc, "type", None) == "billing_error":
        return True
    message = str(getattr(exc, "message", "") or exc).lower()
    return "credit balance is too low" in message


# Web search only localizes for a subset of countries; an unsupported value in
# `user_location` 400s. Once we hit that, stop sending the hint for the rest of
# the process (localization still happens via the prompt + search queries).
_user_location_supported = True


def user_location_supported() -> bool:
    return _user_location_supported


def _disable_user_location() -> None:
    global _user_location_supported
    _user_location_supported = False


def _is_location_error(exc: anthropic.APIStatusError) -> bool:
    message = str(getattr(exc, "message", "") or exc).lower()
    return "user_location" in message or "country code" in message


def _strip_user_location(tools) -> bool:
    """Remove user_location from any tool that has it. True if anything changed."""
    removed = False
    for tool in tools or []:
        if isinstance(tool, dict) and tool.pop("user_location", None) is not None:
            removed = True
    return removed


@dataclass
class AgentResponse:
    """The uniform shape every agent returns to the bot.

    The bot renders this without knowing which agent produced it:
    - `text`  : Telegram-HTML summary (also what gets stored in history).
    - `images`: optional direct image URLs to display alongside the text.
    """

    text: str
    images: list[str] = field(default_factory=list)


def _client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return Anthropic(api_key=api_key)


class BaseAgent(ABC):
    """Base class for all agents.

    Subclasses set the class attributes below and implement
    `format_response()`. They may also override `run()` for fully custom
    behaviour (e.g. a planner that delegates to other agents).
    """

    name: str = "agent"
    # One-line capability summary. Used by the router to decide who handles a
    # request once more than one agent exists.
    description: str = ""
    model: str = DEFAULT_MODEL
    system_prompt: str = ""
    tools: list[dict] | None = None
    max_loops: int = 3
    max_tokens: int = 1536

    def run(
        self,
        text: str,
        history: list[dict] | None = None,
        image_b64: str | None = None,
        media_type: str | None = None,
        location: str | None = None,
    ) -> AgentResponse:
        """Run the agent end to end and return a renderable response.

        `location` is the user's current place (free text); agents may use it to
        localize results. Synchronous and blocking — callers should offload to a
        thread (e.g. asyncio.to_thread) so it never blocks an event loop.
        """
        messages: list[dict] = list(history or [])
        messages.append(
            {"role": "user", "content": self._user_content(text, image_b64, media_type)}
        )
        raw = self._complete(
            messages,
            system=self.build_system(location),
            tools=self.build_tools(location),
        )
        return self.format_response(raw)

    @abstractmethod
    def format_response(self, raw_text: str) -> AgentResponse:
        """Turn the model's final text into an AgentResponse."""

    # Per-request hooks. Override to vary the prompt/tools by request context
    # (e.g. the user's location). Default to the static class attributes.
    def build_system(self, location: str | None) -> str:
        return self.system_prompt

    def build_tools(self, location: str | None) -> list[dict] | None:
        return self.tools

    # --- shared internals --------------------------------------------------

    def _complete(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None,
    ) -> str:
        """Run the agentic loop and return the final assistant text."""
        client = _client()

        for _ in range(self.max_loops):
            kwargs = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            response = self._create(client, **kwargs)
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                return self._extract_text(response.content)
            # tool_use: server-side tools (e.g. web_search) already ran; loop on.

        # Loop budget exhausted — force a final answer with no tools.
        final = self._create(
            client,
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        return self._extract_text(final.content)

    @staticmethod
    def _create(client: Anthropic, **kwargs):
        """messages.create with credit-error translation and location fallback."""
        try:
            return client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            if _is_credit_error(exc):
                raise InsufficientCreditsError(str(exc)) from exc
            # Unsupported user_location: drop it and retry once, and stop
            # sending it for the rest of the process.
            if _is_location_error(exc) and _strip_user_location(kwargs.get("tools")):
                _disable_user_location()
                return client.messages.create(**kwargs)
            raise

    @staticmethod
    def _user_content(text: str, image_b64: str | None, media_type: str | None):
        content: list[dict] = []
        if image_b64:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type or "image/jpeg",
                        "data": image_b64,
                    },
                }
            )
        content.append(
            {"type": "text", "text": text or "Find options based on this image."}
        )
        return content

    @staticmethod
    def _extract_text(content_blocks) -> str:
        parts = [b.text for b in content_blocks if getattr(b, "type", None) == "text"]
        return "\n\n".join(p.strip() for p in parts if p.strip())
