"""Optional LLM reasoning pass for the AI Tech Stack Planner (Phase 2).

Where this module sits in the pipeline::

    user input (goal, budget, timeline, constraints)
        -> rules.py + recommender.py  -> the recommended stack (always, free)
        -> llm.py                     -> extra reasoning about that stack (optional)
        -> report

The rules still decide *what* to recommend.  This module only adds colour to
the report: a short summary, the real risks, a few practical suggestions and a
confidence score.  It is also where the tokens are counted - we estimate the
size of the prompt *before* the call and read the real numbers out of the API
response *afterwards*, so the report can show what the extra reasoning cost.

This is the only module in the project that talks to the network, and it only
does so inside `call_llm` - importing this file never sends anything.  The
default run of the tool therefore stays completely offline and free; the LLM
pass is opt-in.

Everything here uses only the Python standard library, so there is nothing to
install and nothing to upgrade.

The profile and the stack components are *duck typed* on purpose.  We only
read a few attributes from them and we never import the modules that define
them, which keeps this file free of circular imports:

    profile -> .goal, .budget, .timeline_weeks, .constraints
    stack   -> each item has .name, .category, .cost_month
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

#: The OpenAI compatible API we talk to by default.
DEFAULT_BASE_URL = "https://api.openai.com/v1"
#: The small, cheap model we use by default.
DEFAULT_MODEL = "gpt-4o-mini"
#: How long we wait for the model before giving up (seconds).
DEFAULT_TIMEOUT = 30.0
#: The most tokens we are willing to pay for the reply.
MAX_OUTPUT_TOKENS = 400
#: Rough rule of thumb: how many characters fit into one token.
CHARS_PER_TOKEN = 4

#: Width of the separator lines used in the printed report.
WIDTH = 58
RULE = "-" * WIDTH
BAR = "=" * WIDTH

#: The instructions we give the model.  It is deliberately short: every
#: character we send costs tokens, and a short prompt keeps the bill small.
SYSTEM_PROMPT = (
    "You are a cloud architect reviewing an AI project plan. "
    "Reply with one JSON object only, no prose. "
    "The stack is fixed: comment on it, do not redesign it. "
    "Goals: chat=chatbot, rag=docs search, document=PDF processing, "
    "code=coding assistant. "
    "Constraints: needs_api, local_only, enterprise, scale, existing_azure, "
    "open_source. Be brief."
)

#: Matches a fenced code block such as ```json ... ``` so we can look inside.
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\s*(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """Tokens used by one LLM call (from the API response's `usage` block)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMConfig:
    """Where to call the model and with which settings."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    temperature: float = 0.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMConfig":
        """Build a config from LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_TIMEOUT.

        `env` defaults to os.environ. Missing values fall back to the defaults.
        LLM_TIMEOUT is parsed as a float; a bad value falls back to DEFAULT_TIMEOUT.
        """
        source: Mapping[str, str] = os.environ if env is None else env

        # An empty or missing value means "use the default", so a blank
        # environment variable never breaks the planner.
        base_url = (source.get("LLM_BASE_URL") or "").strip() or DEFAULT_BASE_URL
        api_key = (source.get("LLM_API_KEY") or "").strip()
        model = (source.get("LLM_MODEL") or "").strip() or DEFAULT_MODEL

        timeout = DEFAULT_TIMEOUT
        raw_timeout = (source.get("LLM_TIMEOUT") or "").strip()
        if raw_timeout:
            try:
                parsed = float(raw_timeout)
            except ValueError:
                parsed = DEFAULT_TIMEOUT
            # A timeout of zero or less would break the request, so we only
            # accept a positive number.
            timeout = parsed if parsed > 0 else DEFAULT_TIMEOUT

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )

    @property
    def endpoint(self) -> str:
        """`<base_url without trailing slash>/chat/completions`"""
        return f"{self.base_url.rstrip('/')}/chat/completions"


@dataclass
class LLMAdvice:
    """The LLM's structured reply plus the token accounting."""

    summary: str = ""
    risks: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    prompt_estimate: int = 0  # local estimate, counted BEFORE the call
    raw: dict = field(default_factory=dict)


class LLMError(Exception):
    """Raised when the LLM cannot be reached or the reply cannot be parsed."""


# ---------------------------------------------------------------------------
# Counting tokens
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Rough token count: len(text) / CHARS_PER_TOKEN rounded up, 0 for empty text.

    This is only an estimate - the real number arrives with the API response.
    We use it to show how big the prompt was before the call.
    """
    if not text:
        return 0
    # Integer maths, so we never depend on how floats round.
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Building the prompt
# ---------------------------------------------------------------------------


def build_messages(profile: Any, stack: Sequence[Any]) -> list[dict]:
    """Two chat messages: a short system prompt + one compact JSON user message.

    `profile` is duck typed: it needs .goal, .budget, .timeline_weeks, .constraints.
    `stack` items are duck typed: they need .name, .category, .cost_month.
    The user message is a JSON object with the keys:
        goal, budget_usd, weeks, constraints, stack, answer
    `stack` is a list of {name, category, cost_month} objects (no long purpose text -
    that is what keeps the prompt short).
    `answer` is the shape the model must reply with:
        {"summary": "<=2 sentences>", "risks": ["..."], "suggestions": ["..."], "confidence": "low|medium|high"}
    Keep the whole prompt short: under 1500 characters for a normal profile.
    """
    user_payload = {
        "goal": profile.goal,
        "budget_usd": profile.budget,
        "weeks": profile.timeline_weeks,
        "constraints": list(profile.constraints),
        # Only the three short fields per component.  The long `purpose`
        # text is for humans and would make the prompt much more expensive.
        "stack": [
            {
                "name": component.name,
                "category": component.category,
                "cost_month": component.cost_month,
            }
            for component in stack
        ],
        # The shape of the answer we want back.  Showing it to the model
        # saves a round trip of "please try again" and a second round trip
        # of tokens.
        "answer": {
            "summary": "<=2 sentences",
            "risks": ["..."],
            "suggestions": ["..."],
            "confidence": "low|medium|high",
        },
    }

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            # Compact separators (no spaces after the commas and colons) keep
            # the prompt as short as possible.
            "content": json.dumps(user_payload, separators=(",", ":")),
        },
    ]


def _messages_to_text(messages: Sequence[dict]) -> str:
    """Join the contents of the chat messages with newlines."""
    return "\n".join(str(message.get("content", "")) for message in messages)


def build_prompt(profile: Any, stack: Sequence[Any]) -> str:
    """All message contents joined by a newline - used for the pre-call estimate."""
    return _messages_to_text(build_messages(profile, stack))


# ---------------------------------------------------------------------------
# Reading the model's reply
# ---------------------------------------------------------------------------


def _clean_text(value: Any) -> str:
    """Return a cleaned up one-line string ("" for anything that is not text)."""
    if not isinstance(value, str):
        return ""
    # Collapsing the whitespace keeps the summary on a single report line.
    return " ".join(value.split())


def _clean_list(value: Any) -> list[str]:
    """Return a list of clean strings (empty list when the value is not a list)."""
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        text = _clean_text(entry)
        if text:
            items.append(text)
    return items


def _candidate_texts(text: str) -> list[str]:
    """Return the places we should look for the JSON object.

    A fenced block (```json ... ```) is tried first, because that is where a
    model usually puts its JSON.  The whole text is the fallback, which lets us
    find JSON that sits between a bit of prose.
    """
    candidates: list[str] = []
    for block in _FENCE_RE.findall(text):
        if block.strip():
            candidates.append(block)
    candidates.append(text)
    return candidates


def _find_json_object(text: str) -> dict | None:
    """Return the first JSON object found in `text`, or None.

    `json.JSONDecoder().raw_decode` is used because it understands strings, so
    a brace inside a quoted value does not end the object too early.
    """
    decoder = json.JSONDecoder()
    for candidate in _candidate_texts(text):
        index = candidate.find("{")
        while index != -1:
            try:
                value, _ = decoder.raw_decode(candidate, index)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                return value
            index = candidate.find("{", index + 1)
    return None


def parse_advice(text: str) -> dict:
    """Parse the model's reply into the advice dict (keys summary/risks/suggestions/confidence).

    Tolerate ```json fenced blocks and extra prose before/after the JSON object.
    Missing keys become empty defaults; a wrong type falls back to the default too.
    Raises LLMError when no JSON object can be found.
    """
    if not isinstance(text, str) or not text.strip():
        raise LLMError("the model reply was empty")

    data = _find_json_object(text)
    if data is None:
        raise LLMError("the model reply did not contain a JSON object")

    return {
        "summary": _clean_text(data.get("summary")),
        "risks": _clean_list(data.get("risks")),
        "suggestions": _clean_list(data.get("suggestions")),
        "confidence": _clean_text(data.get("confidence")).lower(),
    }


# ---------------------------------------------------------------------------
# The HTTP transport
# ---------------------------------------------------------------------------


def _error_reason(exc: urllib.error.HTTPError) -> str:
    """Pull a short, human readable reason out of an HTTP error body."""
    try:
        text = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:  # pragma: no cover - the body is sometimes already gone
        text = ""

    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()[:200]
            if isinstance(error, str) and error.strip():
                return error.strip()[:200]
        # Not JSON: show the plain text, but keep it short.
        return text[:200]

    return str(exc.reason)[:200] if exc.reason else "no details given"


def _post_json(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    """POST `payload` as JSON and return the parsed JSON body.

    Every problem (no network, timeout, error status, broken body) is turned
    into an LLMError with a short reason, so the caller never has to deal
    with urllib or JSON exceptions.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise LLMError(
            f"the model server replied with HTTP {exc.code}: {_error_reason(exc)}"
        ) from exc
    except urllib.error.URLError as exc:
        # A timeout often arrives wrapped in a URLError, so we look inside.
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise LLMError(f"the request timed out after {timeout} seconds") from exc
        raise LLMError(f"could not reach the model server: {reason}") from exc
    except TimeoutError as exc:
        raise LLMError(f"the request timed out after {timeout} seconds") from exc
    except OSError as exc:
        raise LLMError(f"could not reach the model server: {exc}") from exc

    if not 200 <= int(status) < 300:
        raise LLMError(f"the model server replied with HTTP {status}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("the model server did not return JSON") from exc

    if not isinstance(data, dict):
        raise LLMError("the model server returned JSON that is not an object")

    return data


# ---------------------------------------------------------------------------
# Calling the model
# ---------------------------------------------------------------------------


def _extract_content(body: dict) -> str:
    """Pull the reply text out of an OpenAI compatible response body."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, str):
        return ""
    return content


def _as_int(value: Any) -> int:
    """Turn a number from the API reply into a plain int (0 when it is not one)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _extract_usage(raw: Any) -> TokenUsage:
    """Read the `usage` block of the response into a TokenUsage."""
    if not isinstance(raw, dict):
        return TokenUsage()

    prompt_tokens = _as_int(raw.get("prompt_tokens"))
    completion_tokens = _as_int(raw.get("completion_tokens"))
    total_tokens = _as_int(raw.get("total_tokens"))
    if not total_tokens:
        # Some servers only send the two parts; the total is their sum.
        total_tokens = prompt_tokens + completion_tokens

    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def call_llm(
    config: LLMConfig,
    profile: Any,
    stack: Sequence[Any],
    transport: Callable[[str, dict, dict, float], dict] | None = None,
) -> LLMAdvice:
    """One LLM round trip, then parse the reply.

    `transport(url, headers, payload, timeout) -> dict` does the HTTP POST and returns the
    parsed JSON body. The default implementation is the real HTTP call; tests inject a fake.
    Request payload keys: model, messages, temperature, max_tokens, response_format
    (response_format is {"type": "json_object"}).
    Response body shape (OpenAI compatible):
        {"choices": [{"message": {"content": "<json text>"}}],
         "usage": {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}}
    The usage numbers end up in LLMAdvice.usage, the local pre-call estimate in
    LLMAdvice.prompt_estimate. Raises LLMError on any failure.
    """
    messages = build_messages(profile, stack)

    # The estimate is counted BEFORE the call, so we always know what the
    # prompt looked like even when the call itself fails.
    prompt_estimate = estimate_tokens(_messages_to_text(messages))

    headers = {"Content-Type": "application/json"}
    api_key = (config.api_key or "").strip()
    if api_key:
        # Only sent when there is a key: a local Ollama server needs none.
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
    }

    send = transport if transport is not None else _post_json
    try:
        body = send(config.endpoint, headers, payload, config.timeout)
    except LLMError:
        raise  # already a friendly message, keep it as it is
    except Exception as exc:
        raise LLMError(f"the request to the model failed: {exc}") from exc

    if not isinstance(body, dict):
        raise LLMError("the model reply was not a JSON object")

    content = _extract_content(body)
    if not content.strip():
        raise LLMError("the model reply did not contain any text")

    advice = parse_advice(content)

    return LLMAdvice(
        summary=advice["summary"],
        risks=advice["risks"],
        suggestions=advice["suggestions"],
        confidence=advice["confidence"],
        usage=_extract_usage(body.get("usage")),
        prompt_estimate=prompt_estimate,
        raw=body,
    )


# ---------------------------------------------------------------------------
# The printed block
# ---------------------------------------------------------------------------


def format_advice(advice: LLMAdvice) -> str:
    """Plain-text block for the report, in the same style as the rules report
    (58 character wide separator lines of '-' and '=')."""
    summary = _clean_text(advice.summary) or "(none)"
    confidence = _clean_text(advice.confidence) or "unknown"

    lines: list[str] = []
    lines.append(BAR)
    lines.append("LLM reasoning")
    lines.append(RULE)
    lines.append(f"Summary:     {summary}")
    lines.append(f"Confidence:  {confidence}")

    # An empty section is left out completely - no header, no bullets.
    risks = [_clean_text(risk) for risk in (advice.risks or [])]
    risks = [risk for risk in risks if risk]
    if risks:
        lines.append("Risks:")
        for risk in risks:
            lines.append(f"  - {risk}")

    suggestions = [_clean_text(suggestion) for suggestion in (advice.suggestions or [])]
    suggestions = [suggestion for suggestion in suggestions if suggestion]
    if suggestions:
        lines.append("Suggestions:")
        for suggestion in suggestions:
            lines.append(f"  - {suggestion}")

    lines.append(BAR)

    return "\n".join(lines)
