"""Stateless model calls — the transforms.

Intelligence about *how* to do things lives in the frozen model. No call knows
any other call happened except through what is written in the files. Handler
calls are isolated: no shared conversation, one request–response per activation.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

# The frozen model. Intelligence about how to do things lives here.
DEFAULT_MODEL = "claude-opus-4-8"


class Model(Protocol):
    def complete(self, system: str, user: str) -> str:  # pragma: no cover - protocol
        ...


class AnthropicModel:
    """A real, stateless Anthropic-backed transform.

    One request–response per activation. The SDK is imported lazily
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
    channel is acted upon; free-text reasoning around it is ignored.
    """
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    for candidate in (fence.group(1) if fence else None, text.strip()):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        # Valid JSON but the wrong shape (commonly a bare array of updates).
        # Refusing here matters: scanning on would find the first `{` *inside*
        # the array and silently return one element as the whole response.
        raise ValueError(f"structured channel is a JSON {type(parsed).__name__}, not an object")

    start = text.find("{")
    while start != -1:
        end = _balanced_end(text, start)
        if end is not None:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model response")


def _balanced_end(text: str, start: int) -> int | None:
    """Index of the ``}`` closing the object opened at ``start``, or None.

    Brace counting MUST respect JSON string context: a handler's ``diff`` value
    routinely contains braces (``+    d = {``) and quotes, and a scanner blind
    to strings terminates the object early, producing invalid JSON.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None
