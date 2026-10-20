"""Tests for the optional LLM pass (ai-tech-stack-planner/src/llm.py).

Phase 2 adds a thin, optional layer on top of the deterministic Phase 1
planner: the profile and the recommended stack are turned into a prompt, an
OpenAI compatible chat completion is requested, and the JSON answer comes back
as advice.

Nothing in here touches the network.  Every request goes through an injected
fake transport, so the tests only pin down the contract of the module.
"""

import json
import os
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

# Make the sibling src/ modules importable without installing anything.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import llm  # noqa: E402  (import after the sys.path tweak)

#: The JSON answer a fake model returns.
FAKE_REPLY = {
    "summary": "A budget friendly stack that stays under the monthly cap.",
    "risks": ["Vendor lock-in if the hosted model is picked too early."],
    "suggestions": ["Start with the local model and move to the API later."],
    "confidence": "high",
}

#: The body a fake transport returns (OpenAI compatible).
FAKE_BODY = {
    "choices": [{"message": {"content": json.dumps(FAKE_REPLY)}}],
    "usage": {"prompt_tokens": 321, "completion_tokens": 88, "total_tokens": 409},
}


@dataclass
class FakeProfile:
    """A stand-in for recommender.ProjectProfile."""

    goal: str = "chat"
    budget: float = 100.0
    timeline_weeks: int = 4
    constraints: list = field(default_factory=list)


@dataclass
class FakeComponent:
    """A stand-in for rules.Component (only the fields the LLM gets to see)."""

    name: str
    category: str
    cost_month: float = 0.0


class RecordingTransport:
    """A fake transport: records the call and returns a canned body.

    The transport is called as ``transport(url, headers, payload, timeout)``.
    Both positional and keyword calls are accepted so that the tests do not
    depend on the exact call style of the module.
    """

    def __init__(self, body):
        self.body = body
        self.calls = []
        self.url = None
        self.headers = {}
        self.payload = {}
        self.timeout = None

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        names = ("url", "headers", "payload", "timeout")
        values = dict(zip(names, args))
        for name in names:
            if name in kwargs:
                values[name] = kwargs[name]
        self.url = values.get("url")
        self.headers = values.get("headers") or {}
        self.payload = values.get("payload") or {}
        self.timeout = values.get("timeout")
        return self.body


def header_of(headers, name):
    """Return a header value (case insensitively) or None when it is absent."""
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def sample_profile(**overrides):
    """A realistic profile the tests can tweak with keyword arguments."""
    profile = FakeProfile(
        goal="rag",
        budget=100,
        timeline_weeks=8,
        constraints=["needs_api", "existing_azure"],
    )
    for key, value in overrides.items():
        setattr(profile, key, value)
    return profile


def sample_stack():
    """A realistic stack of five components."""
    return [
        FakeComponent("Azure OpenAI", "model", 25.0),
        FakeComponent("Azure App Service", "hosting", 30.0),
        FakeComponent("Azure AI Search", "data", 100.0),
        FakeComponent("Azure Functions", "hosting", 5.0),
        FakeComponent("Azure Key Vault", "ops", 5.0),
    ]


class TestConstants(unittest.TestCase):
    """The documented module level constants."""

    def test_documented_constants(self):
        self.assertEqual(llm.DEFAULT_BASE_URL, "https://api.openai.com/v1")
        self.assertEqual(llm.DEFAULT_MODEL, "gpt-4o-mini")
        self.assertEqual(llm.DEFAULT_TIMEOUT, 30.0)
        self.assertEqual(llm.MAX_OUTPUT_TOKENS, 400)
        self.assertEqual(llm.CHARS_PER_TOKEN, 4)


class TestEstimateTokens(unittest.TestCase):
    """A cheap local estimate: four characters per token, rounded up."""

    def test_empty_string_is_zero_tokens(self):
        self.assertEqual(llm.estimate_tokens(""), 0, "an empty string costs no tokens")

    def test_one_character_is_one_token(self):
        self.assertEqual(llm.estimate_tokens("a"), 1, "the estimate rounds up")

    def test_four_characters_are_one_token(self):
        self.assertEqual(llm.estimate_tokens("abcd"), 1, "4 characters should be 1 token")

    def test_five_characters_are_two_tokens(self):
        self.assertEqual(
            llm.estimate_tokens("abcde"),
            2,
            "5 characters should round up to 2 tokens",
        )

    def test_four_hundred_characters_are_one_hundred_tokens(self):
        self.assertEqual(
            llm.estimate_tokens("x" * 400),
            100,
            "400 characters should be 100 tokens",
        )

    def test_more_text_means_more_tokens(self):
        short = llm.estimate_tokens("x" * 100)
        long = llm.estimate_tokens("x" * 1000)

        self.assertGreater(long, short, "a longer prompt must cost more tokens")


class TestBuildMessages(unittest.TestCase):
    """The two messages that are sent to the model."""

    def setUp(self):
        self.profile = sample_profile()
        self.stack = sample_stack()
        self.messages = llm.build_messages(self.profile, self.stack)

    def user_payload(self):
        """The parsed JSON of the user message."""
        return json.loads(self.messages[1]["content"])

    def test_exactly_two_messages(self):
        self.assertEqual(
            len(self.messages),
            2,
            "the request needs exactly a system and a user message",
        )

    def test_first_message_is_the_system_message(self):
        self.assertEqual(self.messages[0].get("role"), "system", "message 0 must be the system message")
        self.assertTrue(
            str(self.messages[0].get("content", "")).strip(),
            "the system message needs some text",
        )

    def test_second_message_is_the_user_message(self):
        self.assertEqual(self.messages[1].get("role"), "user", "message 1 must be the user message")
        self.assertTrue(
            str(self.messages[1].get("content", "")).strip(),
            "the user message needs some text",
        )

    def test_user_message_is_valid_json(self):
        try:
            payload = self.user_payload()
        except json.JSONDecodeError as exc:
            self.fail(f"the user message must be valid JSON: {exc}")

        self.assertIsInstance(payload, dict)

    def test_user_message_has_the_documented_keys(self):
        payload = self.user_payload()

        for key in ("goal", "budget_usd", "weeks", "constraints", "stack", "answer"):
            self.assertIn(key, payload, f"the user message should carry '{key}'")

    def test_user_message_carries_the_profile_values(self):
        payload = self.user_payload()

        self.assertEqual(payload["goal"], self.profile.goal)
        self.assertEqual(payload["budget_usd"], self.profile.budget)
        self.assertEqual(payload["weeks"], self.profile.timeline_weeks)
        self.assertIn("needs_api", payload["constraints"])

    def test_stack_entries_only_carry_the_documented_fields(self):
        payload = self.user_payload()

        self.assertEqual(len(payload["stack"]), len(self.stack), "every component should be listed")
        for entry, component in zip(payload["stack"], self.stack):
            self.assertEqual(
                set(entry),
                {"name", "category", "cost_month"},
                "a stack entry should only carry name, category and cost_month",
            )
            self.assertEqual(entry["name"], component.name)
            self.assertEqual(entry["category"], component.category)
            self.assertEqual(entry["cost_month"], component.cost_month)

    def test_answer_is_the_documented_skeleton(self):
        answer = self.user_payload()["answer"]

        for key in ("summary", "risks", "suggestions", "confidence"):
            self.assertIn(key, answer, f"the answer skeleton should carry '{key}'")
        self.assertIsInstance(answer["risks"], list, "risks should be a list")
        self.assertIsInstance(answer["suggestions"], list, "suggestions should be a list")
        self.assertIsInstance(answer["summary"], str, "summary should be a string")
        self.assertIsInstance(answer["confidence"], str, "confidence should be a string")


class TestBuildPrompt(unittest.TestCase):
    """The prompt is the two message contents joined by newlines."""

    def setUp(self):
        self.profile = sample_profile()
        self.stack = sample_stack()
        self.messages = llm.build_messages(self.profile, self.stack)
        self.prompt = llm.build_prompt(self.profile, self.stack)

    def test_prompt_contains_the_system_message(self):
        self.assertIn(self.messages[0]["content"], self.prompt, "the system message is missing")

    def test_prompt_contains_the_user_message(self):
        self.assertIn(self.messages[1]["content"], self.prompt, "the user message is missing")

    def test_prompt_is_the_messages_joined_by_newlines(self):
        expected = "\n".join(message["content"] for message in self.messages)

        self.assertEqual(self.prompt, expected, "the prompt should join the messages with a newline")

    def test_prompt_stays_small_for_a_realistic_profile(self):
        self.assertLess(
            len(self.prompt),
            1500,
            "the prompt should stay well under the context window",
        )


class TestParseAdvice(unittest.TestCase):
    """Turning the model's text into a dictionary of advice."""

    def test_plain_json_object(self):
        advice = llm.parse_advice(json.dumps(FAKE_REPLY))

        self.assertEqual(advice["summary"], FAKE_REPLY["summary"])
        self.assertEqual(advice["risks"], FAKE_REPLY["risks"])
        self.assertEqual(advice["suggestions"], FAKE_REPLY["suggestions"])
        self.assertEqual(advice["confidence"], FAKE_REPLY["confidence"])

    def test_fenced_json_block(self):
        text = "```json\n" + json.dumps(FAKE_REPLY) + "\n```"

        advice = llm.parse_advice(text)

        self.assertEqual(advice["summary"], FAKE_REPLY["summary"])
        self.assertEqual(advice["risks"], FAKE_REPLY["risks"])
        self.assertEqual(advice["suggestions"], FAKE_REPLY["suggestions"])
        self.assertEqual(advice["confidence"], FAKE_REPLY["confidence"])

    def test_json_surrounded_by_prose(self):
        text = "Sure, here is my advice:\n" + json.dumps(FAKE_REPLY) + "\nHope that helps!"

        advice = llm.parse_advice(text)

        self.assertEqual(advice["summary"], FAKE_REPLY["summary"])
        self.assertEqual(advice["risks"], FAKE_REPLY["risks"])
        self.assertEqual(advice["suggestions"], FAKE_REPLY["suggestions"])
        self.assertEqual(advice["confidence"], FAKE_REPLY["confidence"])

    def test_missing_keys_get_empty_defaults(self):
        advice = llm.parse_advice('{"summary": "Only a summary."}')

        self.assertEqual(advice["summary"], "Only a summary.")
        self.assertEqual(advice["risks"], [], "a missing risks key should default to an empty list")
        self.assertEqual(
            advice["suggestions"],
            [],
            "a missing suggestions key should default to an empty list",
        )
        self.assertIsInstance(advice["confidence"], str, "confidence should always be a string")

    def test_text_without_json_raises_an_llm_error(self):
        with self.assertRaises(llm.LLMError, msg="text without JSON must raise LLMError"):
            llm.parse_advice("I am afraid I cannot help with that.")

    def test_empty_text_raises_an_llm_error(self):
        with self.assertRaises(llm.LLMError, msg="an empty reply must raise LLMError"):
            llm.parse_advice("")


class TestLLMConfig(unittest.TestCase):
    """Where the endpoint, the key and the model come from."""

    def test_defaults(self):
        config = llm.LLMConfig()

        self.assertEqual(config.base_url, llm.DEFAULT_BASE_URL)
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.model, llm.DEFAULT_MODEL)
        self.assertEqual(config.timeout, llm.DEFAULT_TIMEOUT)
        self.assertEqual(config.temperature, 0.0)

    def test_from_env_reads_every_value(self):
        env = {
            "LLM_BASE_URL": "https://example.invalid/v1",
            "LLM_API_KEY": "sk-test-123",
            "LLM_MODEL": "gpt-4o",
            "LLM_TIMEOUT": "45.5",
        }

        config = llm.LLMConfig.from_env(env)

        self.assertEqual(config.base_url, "https://example.invalid/v1")
        self.assertEqual(config.api_key, "sk-test-123")
        self.assertEqual(config.model, "gpt-4o")
        self.assertEqual(config.timeout, 45.5, "the timeout should be read as a number")

    def test_from_env_falls_back_to_the_defaults(self):
        config = llm.LLMConfig.from_env({})

        self.assertEqual(config.base_url, llm.DEFAULT_BASE_URL)
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.model, llm.DEFAULT_MODEL)
        self.assertEqual(config.timeout, llm.DEFAULT_TIMEOUT)

    def test_from_env_without_arguments_reads_the_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = llm.LLMConfig.from_env()

        self.assertEqual(config.base_url, llm.DEFAULT_BASE_URL)
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.model, llm.DEFAULT_MODEL)
        self.assertEqual(config.timeout, llm.DEFAULT_TIMEOUT)

    def test_endpoint_appends_the_chat_completions_path(self):
        config = llm.LLMConfig(base_url="https://example.invalid/v1")

        self.assertEqual(
            config.endpoint,
            "https://example.invalid/v1/chat/completions",
            "the endpoint should point at the chat completions route",
        )

    def test_endpoint_of_the_default_config(self):
        self.assertEqual(
            llm.LLMConfig().endpoint,
            "https://api.openai.com/v1/chat/completions",
            "the default endpoint should point at the OpenAI chat completions route",
        )

    def test_endpoint_never_doubles_the_slash(self):
        config = llm.LLMConfig(base_url="https://example.invalid/v1/")

        self.assertEqual(
            config.endpoint,
            "https://example.invalid/v1/chat/completions",
            "a trailing slash must not be doubled",
        )
        self.assertNotIn("//chat", config.endpoint, "the endpoint must not contain a double slash")


class TestCallLLM(unittest.TestCase):
    """The request that is sent and the advice that comes back."""

    def setUp(self):
        self.profile = sample_profile()
        self.stack = sample_stack()
        self.config = llm.LLMConfig(api_key="sk-secret", model="gpt-4o-mini", timeout=12.0)

    def call(self, transport, config=None):
        """Call the module with the given fake transport."""
        return llm.call_llm(
            config or self.config,
            self.profile,
            self.stack,
            transport=transport,
        )

    def test_returns_an_llm_advice(self):
        advice = self.call(RecordingTransport(FAKE_BODY))

        self.assertIsInstance(advice, llm.LLMAdvice)

    def test_advice_comes_from_the_reply(self):
        advice = self.call(RecordingTransport(FAKE_BODY))

        self.assertEqual(advice.summary, FAKE_REPLY["summary"])
        self.assertEqual(advice.risks, FAKE_REPLY["risks"])
        self.assertEqual(advice.suggestions, FAKE_REPLY["suggestions"])
        self.assertEqual(advice.confidence, FAKE_REPLY["confidence"])

    def test_usage_comes_from_the_body(self):
        advice = self.call(RecordingTransport(FAKE_BODY))

        self.assertIsInstance(advice.usage, llm.TokenUsage)
        self.assertEqual(advice.usage.prompt_tokens, 321)
        self.assertEqual(advice.usage.completion_tokens, 88)
        self.assertEqual(advice.usage.total_tokens, 409)

    def test_prompt_estimate_is_counted_before_the_call(self):
        advice = self.call(RecordingTransport(FAKE_BODY))

        self.assertGreater(
            advice.prompt_estimate,
            0,
            "the local token estimate should be positive",
        )

    def test_payload_has_the_documented_keys(self):
        transport = RecordingTransport(FAKE_BODY)

        self.call(transport)

        for key in ("model", "messages", "temperature", "max_tokens", "response_format"):
            self.assertIn(key, transport.payload, f"the payload should carry '{key}'")
        self.assertEqual(transport.payload["model"], self.config.model)
        self.assertEqual(transport.payload["temperature"], self.config.temperature)
        self.assertEqual(transport.payload["max_tokens"], llm.MAX_OUTPUT_TOKENS)
        self.assertEqual(len(transport.payload["messages"]), 2, "both messages should be sent")

    def test_authorization_header_is_sent_when_a_key_is_set(self):
        transport = RecordingTransport(FAKE_BODY)

        self.call(transport)

        self.assertEqual(
            header_of(transport.headers, "authorization"),
            "Bearer sk-secret",
            "the key should be sent as a bearer token",
        )

    def test_authorization_header_is_omitted_without_a_key(self):
        transport = RecordingTransport(FAKE_BODY)

        self.call(transport, config=llm.LLMConfig(api_key=""))

        self.assertIsNone(
            header_of(transport.headers, "authorization"),
            "without a key there should be no Authorization header",
        )

    def test_url_points_at_the_chat_completions_endpoint(self):
        transport = RecordingTransport(FAKE_BODY)

        self.call(transport)

        self.assertTrue(
            str(transport.url).endswith("/chat/completions"),
            f"unexpected url: {transport.url}",
        )

    def test_timeout_is_passed_to_the_transport(self):
        transport = RecordingTransport(FAKE_BODY)

        self.call(transport)

        self.assertEqual(transport.timeout, self.config.timeout, "the config timeout should be passed on")

    def test_transport_error_is_raised_as_an_llm_error(self):
        def boom(url, headers, payload, timeout):
            raise llm.LLMError("the model is down")

        with self.assertRaises(llm.LLMError, msg="a failing transport must raise LLMError"):
            self.call(boom)

    def test_body_without_choices_raises_an_llm_error(self):
        transport = RecordingTransport({"usage": {"prompt_tokens": 1, "total_tokens": 1}})

        with self.assertRaises(llm.LLMError, msg="a body without choices must raise LLMError"):
            self.call(transport)

    def test_body_with_empty_choices_raises_an_llm_error(self):
        transport = RecordingTransport({"choices": [], "usage": {"total_tokens": 1}})

        with self.assertRaises(llm.LLMError, msg="a body without any choice must raise LLMError"):
            self.call(transport)


class TestFormatAdvice(unittest.TestCase):
    """The advice as it is shown to the user."""

    def test_the_text_contains_the_whole_advice(self):
        advice = llm.LLMAdvice(
            summary="A budget friendly stack that stays under the monthly cap.",
            risks=["Vendor lock-in if the hosted model is picked too early."],
            suggestions=["Start with the local model and move to the API later."],
            confidence="high",
        )

        text = llm.format_advice(advice)

        self.assertIsInstance(text, str, "format_advice should return a string")
        self.assertIn(advice.summary, text, "the summary is missing from the advice text")
        self.assertIn(advice.confidence, text, "the confidence is missing from the advice text")
        for risk in advice.risks:
            self.assertIn(risk, text, "a risk is missing from the advice text")
        for suggestion in advice.suggestions:
            self.assertIn(suggestion, text, "a suggestion is missing from the advice text")


if __name__ == "__main__":
    unittest.main()
