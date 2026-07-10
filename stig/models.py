"""Stateless model calls — the transforms (SPEC §01, §07).

Intelligence about *how* to do things lives in the frozen model. No call knows
any other call happened except through what is written in the files. Handler
calls are isolated: no shared conversation, one request–response per activation.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

# The frozen model. Intelligence about how to do things lives here (SPEC §01).
DEFAULT_MODEL = "claude-opus-4-8"


class Model(Protocol):
    def complete(self, system: str, user: str) -> str:  # pragma: no cover - protocol
        ...


class AnthropicModel:
    """A real, stateless Anthropic-backed transform.

    One request–response per activation (SPEC §07). The SDK is imported lazily
    so the rest of Stig runs without the optional dependency.
    """

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 16000):
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # lazy import

            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str) -> str:
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


class ScriptedModel:
    """A deterministic model for tests and dry runs.

    Holds a queue of responses (strings). Each ``complete`` call pops the next
    one. This exercises the entire scheduler machinery without a live call.
    """

    def __init__(self, responses: list[str] | None = None):
        self.responses: list[str] = list(responses or [])
        self.calls: list[tuple[str, str]] = []

    def push(self, response: str) -> None:
        self.responses.append(response)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise RuntimeError("ScriptedModel ran out of responses")
        return self.responses.pop(0)


def extract_json(text: str) -> dict:
    """Extract the handler's structured JSON object from a model response.

    Accepts a ```json fenced block or a bare object. Only the structured
    channel is acted upon; free-text reasoning around it is ignored (SPEC §07).
    """
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    # Fall back to the last balanced top-level object in the text.
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model response")
