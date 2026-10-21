"""Tests for the web interface (ai-tech-stack-planner/src/web.py).

Phase 4 adds a small Flask app in front of the modules that already exist:
the rules still decide the stack, the recommender still works out the cost
and the LLM is still optional.  These tests pin down the JSON contract of the
API, the input checking and the round trip through the saved plans.

The whole module is skipped when Flask is not installed, so the command line
planner keeps working with a standard library only Python.

Every test that touches the disk works inside a temporary folder, so the
real ``plans`` folder in the repository is never written to.  The LLM tests
use a fake transport: no tokens are spent and no network call is made.
"""

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the sibling src/ modules importable without installing anything.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import flask

    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

try:
    import pricing

    PRICING_AVAILABLE = True
except ImportError:
    PRICING_AVAILABLE = False

if FLASK_AVAILABLE:  # pragma: no cover - the import guard above decides
    import web  # noqa: E402  (import after the sys.path tweak)
    from web import create_app  # noqa: E402

    from recommender import ProjectProfile, recommend  # noqa: E402


#: The reply the fake transport gives: an OpenAI compatible body.
DEFAULT_REPLY = {
    "summary": "A cheap stack that starts locally and grows later.",
    "risks": ["The hosted model costs money once the traffic grows."],
    "suggestions": ["Move to the hosted model only when you need it."],
    "confidence": "high",
}

#: The token numbers the fake transport reports.
USAGE = {"prompt_tokens": 189, "completion_tokens": 96, "total_tokens": 285}

#: A profile that is valid and produces a stack with more than one part.
VALID_BODY = {
    "goal": "rag",
    "budget": 200,
    "timeline_weeks": 4,
    "constraints": ["needs_api"],
    "use_llm": False,
}


def fake_transport(reply=None, calls=None):
    """A transport that answers like an OpenAI compatible API.

    `transport(url, headers, payload, timeout) -> dict` is the signature
    llm.call_llm expects.  Every call is appended to `calls` when a list is
    given, so a test can look at what was sent.
    """

    def transport(url, headers, payload, timeout):
        if calls is not None:
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                }
            )
        content = reply if reply is not None else DEFAULT_REPLY
        return {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": dict(USAGE),
        }

    return transport


@contextlib.contextmanager
def llm_environment(api_key="test-key"):
    """Run a block with a known LLM environment.

    `api_key=None` removes LLM_API_KEY, so the "no key" path can be tested.
    Everything is put back when the block ends.
    """
    with mock.patch.dict(os.environ, {}, clear=False):
        if api_key is None:
            os.environ.pop("LLM_API_KEY", None)
        else:
            os.environ["LLM_API_KEY"] = api_key
        yield


@unittest.skipUnless(
    FLASK_AVAILABLE, "Flask is not installed - run: pip install -r requirements.txt"
)
class WebAppTests(unittest.TestCase):
    """The HTTP layer: the page, the options and the planning endpoint."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.plans_dir = Path(self._temp.name)
        self.client = self.make_client()

    def make_client(self, **kwargs):
        """A test client for a fresh app in the temporary plans folder."""
        kwargs.setdefault("plans_dir", self.plans_dir)
        # Live prices are off in the tests: they must never reach the network.
        kwargs.setdefault("pricing_enabled", False)
        app = create_app(**kwargs)
        app.config.update(TESTING=True)
        return app.test_client()

    # --- The page -----------------------------------------------------------

    def test_index_page_shows_the_form(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("<form", body)
        self.assertIn('id="goal"', body)
        self.assertIn('id="budget"', body)
        self.assertIn('id="timeline_weeks"', body)
        self.assertIn('id="constraints"', body)
        self.assertIn('id="use_llm"', body)
        self.assertIn('id="use_pricing"', body)
        self.assertIn('id="save_as"', body)

    def test_static_files_are_served(self):
        for name in ("app.js", "style.css"):
            with self.subTest(name=name):
                response = self.client.get(f"/static/{name}")
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.get_data(as_text=True).strip())
                finally:
                    response.close()

    # --- The options -------------------------------------------------------

    def test_options_lists_the_goals_and_the_constraints(self):
        response = self.client.get("/api/options")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertIn("chat", data["goals"])
        self.assertIn("rag", data["goals"])
        self.assertIn("needs_api", data["constraints"])
        self.assertIn("local_only", data["constraints"])

        self.assertEqual(data["defaults"]["goal"], "chat")
        self.assertEqual(data["defaults"]["budget"], 100)
        self.assertEqual(data["defaults"]["timeline_weeks"], 4)

    # --- Planning ----------------------------------------------------------

    def test_plan_returns_the_stack_the_cost_and_the_notes(self):
        response = self.client.post("/api/plan", json=dict(VALID_BODY))
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["profile"]["goal"], "rag")
        self.assertEqual(data["profile"]["budget"], 200.0)
        self.assertEqual(data["profile"]["timeline_weeks"], 4)
        self.assertEqual(data["profile"]["constraints"], ["needs_api"])

        self.assertTrue(data["stack"])
        self.assertTrue(data["notes"])
        self.assertGreater(data["monthly_cost"], 0)
        self.assertIsNone(data["llm"])
        self.assertEqual(data["llm_error"], "")
        self.assertEqual(data["pricing"], "estimate")
        self.assertEqual(data["saved_as"], "")

        for component in data["stack"]:
            self.assertIn("name", component)
            self.assertIn("category", component)
            self.assertIn("purpose", component)
            self.assertIn("cost_month", component)

    def test_plan_gives_the_same_answer_as_the_command_line(self):
        response = self.client.post("/api/plan", json=dict(VALID_BODY))
        data = response.get_json()

        expected = recommend(
            ProjectProfile(
                goal="rag",
                budget=200.0,
                timeline_weeks=4,
                constraints=["needs_api"],
            )
        )
        self.assertEqual(data["monthly_cost"], expected.monthly_cost)
        self.assertEqual(
            [item["name"] for item in data["stack"]],
            [component.name for component in expected.stack],
        )
        self.assertEqual(
            [item["id"] for item in data["applied_rules"]],
            [rule.id for rule in expected.applied_rules],
        )

    def test_plan_rejects_bad_input(self):
        cases = [
            (
                {"goal": "nope", "budget": 100, "timeline_weeks": 4},
                "goal",
            ),
            (
                {"goal": "chat", "budget": -1, "timeline_weeks": 4},
                "budget",
            ),
            (
                {"goal": "chat", "budget": 100, "timeline_weeks": 0},
                "timeline",
            ),
            (
                {
                    "goal": "chat",
                    "budget": 100,
                    "timeline_weeks": 4,
                    "constraints": ["nope"],
                },
                "constraint",
            ),
            (
                {
                    "goal": "chat",
                    "budget": 100,
                    "timeline_weeks": 4,
                    "save_as": "../escape",
                },
                "name",
            ),
        ]

        for body, word in cases:
            with self.subTest(body=body):
                response = self.client.post("/api/plan", json=body)
                self.assertEqual(response.status_code, 400)
                data = response.get_json()
                self.assertIn("error", data)
                self.assertTrue(data["error"].strip())
                self.assertIn(word, data["error"].lower())

    def test_plan_rejects_a_body_that_is_not_json(self):
        response = self.client.post(
            "/api/plan", data="not json", content_type="text/plain"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    # --- The saved plans ---------------------------------------------------

    def test_save_list_open_and_delete_a_plan(self):
        body = dict(VALID_BODY)
        body["save_as"] = "my-chat"

        response = self.client.post("/api/plan", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["saved_as"], "my-chat")

        # One file per plan, inside the folder that was given.
        files = list(self.plans_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "my-chat.json")

        listing = self.client.get("/api/plans")
        self.assertEqual(listing.status_code, 200)
        plans = listing.get_json()["plans"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["name"], "my-chat")
        self.assertEqual(plans[0]["goal"], "rag")
        self.assertIn("monthly_cost", plans[0])
        self.assertIn("saved_at", plans[0])

        one = self.client.get("/api/plans/my-chat")
        self.assertEqual(one.status_code, 200)
        saved = one.get_json()
        self.assertEqual(saved["name"], "my-chat")
        self.assertEqual(saved["profile"]["goal"], "rag")
        self.assertTrue(saved["stack"])
        self.assertTrue(saved["notes"])
        self.assertIn("saved_at", saved)

        deleted = self.client.delete("/api/plans/my-chat")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["deleted"], "my-chat")
        self.assertFalse((self.plans_dir / "my-chat.json").exists())

        again = self.client.delete("/api/plans/my-chat")
        self.assertEqual(again.status_code, 404)
        self.assertIn("error", again.get_json())

    def test_a_plan_that_does_not_exist_is_404(self):
        response = self.client.get("/api/plans/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())

    def test_the_plan_list_is_empty_when_nothing_was_saved(self):
        response = self.client.get("/api/plans")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["plans"], [])

    # --- The optional live prices ------------------------------------------

    @unittest.skipUnless(
        PRICING_AVAILABLE, "the pricing module is not available"
    )
    def test_live_prices_replace_the_rough_estimates(self):
        quotes = {
            "Azure OpenAI": pricing.Quote(
                component="Azure OpenAI",
                monthly_usd=10.0,
                unit="1M Tokens",
                meter="GPT-5 Nano Inpt Glbl",
                note="1 million input tokens a month.",
                live=True,
            )
        }

        client = self.make_client(pricing_enabled=True)
        body = dict(VALID_BODY)
        body["use_pricing"] = True

        with mock.patch.object(pricing, "get_quotes", return_value=quotes):
            response = client.post("/api/plan", json=body)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["pricing"], "live")
        costs = {item["name"]: item["cost_month"] for item in data["stack"]}
        self.assertEqual(costs["Azure OpenAI"], 10.0)

        # The cost and the tips are worked out again from the live prices.
        rough = recommend(
            ProjectProfile(
                goal="rag",
                budget=200.0,
                timeline_weeks=4,
                constraints=["needs_api"],
            )
        )
        self.assertEqual(data["monthly_cost"], rough.monthly_cost - 25.0 + 10.0)
        self.assertIn("$145/mo", data["notes"][0])

    @unittest.skipUnless(
        PRICING_AVAILABLE, "the pricing module is not available"
    )
    def test_a_broken_price_list_does_not_break_the_planner(self):
        client = self.make_client(pricing_enabled=True)
        body = dict(VALID_BODY)
        body["use_pricing"] = True

        with mock.patch.object(
            pricing, "get_quotes", side_effect=RuntimeError("no price list here")
        ):
            response = client.post("/api/plan", json=body)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["pricing"], "estimate")
        self.assertTrue(data["stack"])
        self.assertGreater(data["monthly_cost"], 0)

    def test_the_prices_stay_rough_when_they_are_not_asked_for(self):
        client = self.make_client(pricing_enabled=True)
        response = client.post("/api/plan", json=dict(VALID_BODY))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["pricing"], "estimate")

    # --- The optional LLM pass --------------------------------------------

    def test_use_llm_without_an_api_key_still_returns_the_plan(self):
        body = dict(VALID_BODY)
        body["use_llm"] = True

        with llm_environment(None):
            response = self.client.post("/api/plan", json=body)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNone(data["llm"])
        self.assertTrue(data["llm_error"].strip())
        # The rules still answer - the LLM never breaks the planner.
        self.assertTrue(data["stack"])
        self.assertTrue(data["notes"])

    def test_use_llm_with_a_fake_transport_returns_the_advice(self):
        calls = []
        client = self.make_client(llm_transport=fake_transport(calls=calls))

        body = dict(VALID_BODY)
        body["use_llm"] = True

        with llm_environment("test-key"):
            response = client.post("/api/plan", json=body)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertIsNotNone(data["llm"])
        self.assertEqual(data["llm"]["summary"], DEFAULT_REPLY["summary"])
        self.assertEqual(data["llm"]["confidence"], "high")
        self.assertEqual(data["llm"]["risks"], DEFAULT_REPLY["risks"])
        self.assertEqual(data["llm"]["suggestions"], DEFAULT_REPLY["suggestions"])
        self.assertGreater(data["llm"]["prompt_estimate"], 0)

        usage = data["llm"]["usage"]
        self.assertEqual(usage["prompt_tokens"], 189)
        self.assertEqual(usage["completion_tokens"], 96)
        self.assertEqual(usage["total_tokens"], 285)

        self.assertEqual(data["llm_error"], "")
        self.assertEqual(len(calls), 1)
        self.assertIn("model", calls[0]["payload"])

    def test_a_failing_llm_does_not_break_the_planner(self):
        def broken_transport(url, headers, payload, timeout):
            raise OSError("no network here")

        client = self.make_client(llm_transport=broken_transport)

        body = dict(VALID_BODY)
        body["use_llm"] = True

        with llm_environment("test-key"):
            response = client.post("/api/plan", json=body)

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNone(data["llm"])
        self.assertTrue(data["llm_error"].strip())
        self.assertTrue(data["stack"])

    # --- Refreshing the advice of a saved plan ----------------------------

    def test_refresh_runs_the_llm_again_and_writes_the_answer_back(self):
        body = dict(VALID_BODY)
        body["save_as"] = "my-chat"
        self.assertEqual(self.client.post("/api/plan", json=body).status_code, 200)

        client = self.make_client(llm_transport=fake_transport())

        with llm_environment("test-key"):
            response = client.post("/api/plans/my-chat/refresh")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["name"], "my-chat")
        self.assertIsNotNone(data["llm"])
        self.assertEqual(data["llm"]["summary"], DEFAULT_REPLY["summary"])
        self.assertEqual(data["llm"]["usage"]["total_tokens"], 285)

        # The new advice is in the file on disk as well.
        on_disk = json.loads(
            (self.plans_dir / "my-chat.json").read_text(encoding="utf-8")
        )
        self.assertIsNotNone(on_disk["llm"])
        self.assertEqual(on_disk["llm"]["summary"], DEFAULT_REPLY["summary"])

        # The plan itself is untouched.
        opened = client.get("/api/plans/my-chat")
        self.assertEqual(opened.status_code, 200)
        self.assertTrue(opened.get_json()["stack"])

    def test_refresh_without_an_api_key_is_502(self):
        body = dict(VALID_BODY)
        body["save_as"] = "my-chat"
        self.assertEqual(self.client.post("/api/plan", json=body).status_code, 200)

        with llm_environment(None):
            response = self.client.post("/api/plans/my-chat/refresh")

        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.get_json())

    def test_refresh_of_a_plan_that_does_not_exist_is_404(self):
        response = self.client.post("/api/plans/nope/refresh")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()
