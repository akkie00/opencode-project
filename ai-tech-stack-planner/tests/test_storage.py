"""Tests for the plan storage module (ai-tech-stack-planner/src/storage.py).

Phase 3 adds persistence: every plan is written to ONE JSON file inside a
``plans`` folder and can be read back later.  The tests below pin down the
file schema, the round trip through the disk and the error cases.

Every test that touches the disk works inside a temporary folder, so the real
``plans`` folder in the repository is never written to.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# Make the sibling src/ modules importable without installing anything.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import llm  # noqa: E402  (import after the sys.path tweak)
import storage  # noqa: E402  (import after the sys.path tweak)
from recommender import ProjectProfile, Recommendation  # noqa: E402
from rules import Component, Rule  # noqa: E402

#: The repository root - the folder that holds "ai-tech-stack-planner".
REPO_ROOT = Path(__file__).resolve().parents[2]


def sample_profile(**overrides):
    """A realistic profile the tests can tweak with keyword arguments."""
    profile = ProjectProfile(
        goal="rag",
        budget=200.0,
        timeline_weeks=8,
        constraints=["needs_api", "existing_azure"],
    )
    for key, value in overrides.items():
        setattr(profile, key, value)
    return profile


def sample_stack():
    """Three components with different purposes and costs."""
    return [
        Component("Azure OpenAI", "model", "Hosted small model", 25.0),
        Component("Azure Functions", "hosting", "Serverless API layer", 5.0),
        Component("ChromaDB", "data", "Local vector store", 0.0),
    ]


def sample_rules():
    """Two applied rules, each with a note."""
    return [
        Rule(
            id="needs_api",
            description="Add a hosted model plus a serverless API layer.",
            trigger="needs_api",
            add=(Component("Azure OpenAI", "model", "Hosted small model", 25.0),),
            remove=(),
            note="A small hosted model keeps the API cheap at low traffic.",
        ),
        Rule(
            id="local_only",
            description="Drop the cloud components.",
            trigger="local_only",
            add=(Component("ChromaDB", "data", "Local vector store", 0.0),),
            remove=("Azure OpenAI",),
            note="No data leaves your machine.",
        ),
    ]


def sample_recommendation(profile=None, **overrides):
    """A recommendation with a stack, two rules and a few notes."""
    rec = Recommendation(
        profile=profile if profile is not None else sample_profile(),
        stack=sample_stack(),
        monthly_cost=30.0,
        applied_rules=sample_rules(),
        notes=[
            "Estimated $30.00/mo vs your $200.00/mo budget - under budget.",
            "Timeline is standard - managed services with room to tune fit well.",
        ],
    )
    for key, value in overrides.items():
        setattr(rec, key, value)
    return rec


def sample_advice():
    """A piece of LLM advice, including the token numbers."""
    return llm.LLMAdvice(
        summary="A budget friendly stack that stays under the monthly cap.",
        risks=["Vendor lock-in if the hosted model is picked too early."],
        suggestions=["Start with the local model and move to the API later."],
        confidence="high",
        usage=llm.TokenUsage(prompt_tokens=321, completion_tokens=88, total_tokens=409),
        prompt_estimate=120,
        raw={"source": "test"},
    )


class TestValidPlanName(unittest.TestCase):
    """Which names may be used for a plan."""

    def test_plain_names_are_accepted(self):
        for name in ("my-chat", "plan_1", "Rag2026"):
            with self.subTest(name=name):
                self.assertTrue(
                    storage.valid_plan_name(name),
                    f"'{name}' should be a valid plan name",
                )

    def test_unsafe_names_are_rejected(self):
        for name in ("", "..", "a/b", "a\\b", "with space", "-leading"):
            with self.subTest(name=name):
                self.assertFalse(
                    storage.valid_plan_name(name),
                    f"'{name}' should not be a valid plan name",
                )

    def test_a_name_longer_than_64_characters_is_rejected(self):
        self.assertFalse(
            storage.valid_plan_name("a" * 65),
            "a name of 65 characters is too long",
        )

    def test_a_name_of_exactly_64_characters_is_accepted(self):
        self.assertTrue(
            storage.valid_plan_name("a" * 64),
            "a name of 64 characters is still allowed",
        )


class TestPlanPath(unittest.TestCase):
    """Where the file for one plan lives."""

    def test_the_path_is_inside_the_plans_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            plans_dir = Path(tmp)

            path = storage.plan_path(plans_dir, "my-chat")

            self.assertEqual(
                path.parent,
                plans_dir,
                "the plan file should sit directly in the plans folder",
            )

    def test_the_path_carries_the_plan_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = storage.plan_path(Path(tmp), "my-chat")

            self.assertIn(
                "my-chat",
                path.name,
                "the file name should contain the plan name",
            )


class TestSavePlan(unittest.TestCase):
    """Writing a plan to disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plans_dir = Path(self.tmp.name) / "plans"

    def test_the_folder_is_created_when_it_is_missing(self):
        self.assertFalse(self.plans_dir.exists(), "the plans folder should not exist yet")

        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())

        self.assertTrue(
            self.plans_dir.is_dir(),
            "save_plan should create the plans folder when it is missing",
        )

    def test_the_returned_path_is_the_file_that_was_written(self):
        path = storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())

        self.assertTrue(path.is_file(), "save_plan should return the path of the written file")
        files = list(self.plans_dir.iterdir())
        self.assertEqual(len(files), 1, "saving one plan should write exactly one file")
        self.assertEqual(
            files[0].resolve(),
            path.resolve(),
            "the returned path should be the file that was written",
        )

    def test_the_plan_name_is_stored_in_the_file(self):
        path = storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())
        data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["name"], "my-chat", "the file should remember the plan name")

    def test_an_invalid_name_raises_a_plan_error(self):
        for name in ("", "..", "a/b", "a\\b", "with space", "-leading", "a" * 65):
            with self.subTest(name=name):
                with self.assertRaises(
                    storage.PlanError,
                    msg=f"'{name}' should not be usable as a plan name",
                ):
                    storage.save_plan(self.plans_dir, name, sample_recommendation())

    def test_nothing_is_written_outside_the_plans_folder(self):
        with self.assertRaises(storage.PlanError):
            storage.save_plan(self.plans_dir, "../escape", sample_recommendation())

        strays = [entry for entry in Path(self.tmp.name).iterdir() if entry.name != "plans"]
        self.assertEqual(strays, [], "an invalid name must not write outside the plans folder")


class TestSaveAndLoad(unittest.TestCase):
    """The round trip: save a plan, read it back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plans_dir = Path(self.tmp.name) / "plans"

    def test_the_profile_survives_the_round_trip(self):
        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())

        plan = storage.load_plan(self.plans_dir, "my-chat")

        self.assertIsInstance(plan, storage.SavedPlan)
        self.assertEqual(plan.profile.goal, "rag", "the goal should survive")
        self.assertEqual(plan.profile.budget, 200.0, "the budget should survive")
        self.assertEqual(plan.profile.timeline_weeks, 8, "the timeline should survive")
        self.assertEqual(
            list(plan.profile.constraints),
            ["needs_api", "existing_azure"],
            "the constraints should survive",
        )

    def test_every_component_survives_the_round_trip(self):
        rec = sample_recommendation()

        storage.save_plan(self.plans_dir, "my-chat", rec)
        plan = storage.load_plan(self.plans_dir, "my-chat")

        self.assertEqual(len(plan.recommendation.stack), len(rec.stack), "every component should come back")
        for saved, original in zip(plan.recommendation.stack, rec.stack):
            self.assertEqual(saved.name, original.name, "the component name should survive")
            self.assertEqual(saved.category, original.category, "the component category should survive")
            self.assertEqual(saved.purpose, original.purpose, "the component purpose should survive")
            self.assertEqual(saved.cost_month, original.cost_month, "the component cost should survive")

    def test_the_cost_and_the_notes_survive_the_round_trip(self):
        rec = sample_recommendation()

        storage.save_plan(self.plans_dir, "my-chat", rec)
        plan = storage.load_plan(self.plans_dir, "my-chat")

        self.assertAlmostEqual(
            plan.recommendation.monthly_cost,
            rec.monthly_cost,
            places=2,
            msg="the monthly cost should survive",
        )
        self.assertEqual(
            list(plan.recommendation.notes),
            list(rec.notes),
            "the notes should survive",
        )

    def test_every_applied_rule_survives_the_round_trip(self):
        rec = sample_recommendation()

        storage.save_plan(self.plans_dir, "my-chat", rec)
        plan = storage.load_plan(self.plans_dir, "my-chat")

        self.assertEqual(
            len(plan.recommendation.applied_rules),
            len(rec.applied_rules),
            "every applied rule should come back",
        )
        for saved, original in zip(plan.recommendation.applied_rules, rec.applied_rules):
            self.assertEqual(saved.id, original.id, "the rule id should survive")
            self.assertEqual(saved.description, original.description, "the rule description should survive")
            self.assertEqual(saved.trigger, original.trigger, "the rule trigger should survive")
            self.assertEqual(saved.note, original.note, "the rule note should survive")

    def test_the_llm_advice_survives_the_round_trip(self):
        advice = sample_advice()

        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation(), advice)
        plan = storage.load_plan(self.plans_dir, "my-chat")

        self.assertIsNotNone(plan.llm, "the saved advice should come back")
        self.assertEqual(plan.llm.summary, advice.summary, "the summary should survive")
        self.assertEqual(list(plan.llm.risks), list(advice.risks), "the risks should survive")
        self.assertEqual(list(plan.llm.suggestions), list(advice.suggestions), "the suggestions should survive")
        self.assertEqual(plan.llm.confidence, advice.confidence, "the confidence should survive")
        self.assertEqual(plan.llm.prompt_estimate, advice.prompt_estimate, "the prompt estimate should survive")
        self.assertEqual(plan.llm.usage.prompt_tokens, 321, "the prompt tokens should survive")
        self.assertEqual(plan.llm.usage.completion_tokens, 88, "the completion tokens should survive")
        self.assertEqual(plan.llm.usage.total_tokens, 409, "the total tokens should survive")

    def test_no_llm_pass_means_no_advice(self):
        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())

        plan = storage.load_plan(self.plans_dir, "my-chat")

        self.assertIsNone(plan.llm, "a plan saved without an LLM pass has no advice")

    def test_the_name_and_the_saved_time_survive(self):
        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())

        plan = storage.load_plan(self.plans_dir, "my-chat")

        self.assertEqual(plan.name, "my-chat", "the plan name should survive")
        self.assertTrue(plan.saved_at, "the plan should carry a saved time")
        try:
            datetime.fromisoformat(plan.saved_at)
        except ValueError as exc:
            self.fail(f"saved_at should be an ISO 8601 timestamp: {exc}")

    def test_the_file_is_one_json_object_with_the_documented_keys(self):
        path = storage.save_plan(
            self.plans_dir,
            "my-chat",
            sample_recommendation(),
            sample_advice(),
        )

        files = list(self.plans_dir.iterdir())
        self.assertEqual(len(files), 1, "saving a plan should write exactly one file")

        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict, "the plan file should hold a JSON object")
        for key in ("schema_version", "name", "saved_at", "profile", "recommendation", "llm"):
            self.assertIn(key, data, f"the plan file should carry '{key}'")
        self.assertEqual(
            data["schema_version"],
            storage.SCHEMA_VERSION,
            "the file should carry the current schema version",
        )

    def test_the_profile_section_has_the_documented_keys(self):
        path = storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())
        data = json.loads(path.read_text(encoding="utf-8"))

        for key in ("goal", "budget", "timeline_weeks", "constraints"):
            self.assertIn(key, data["profile"], f"the profile should carry '{key}'")

    def test_the_recommendation_section_has_the_documented_keys(self):
        path = storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())
        data = json.loads(path.read_text(encoding="utf-8"))
        recommendation = data["recommendation"]

        for key in ("monthly_cost", "stack", "applied_rules", "notes"):
            self.assertIn(key, recommendation, f"the recommendation should carry '{key}'")
        for entry in recommendation["stack"]:
            for key in ("name", "category", "purpose", "cost_month"):
                self.assertIn(key, entry, f"a stack entry should carry '{key}'")
        for entry in recommendation["applied_rules"]:
            for key in ("id", "description", "trigger", "note"):
                self.assertIn(key, entry, f"an applied rule should carry '{key}'")

    def test_the_llm_section_has_the_documented_keys(self):
        path = storage.save_plan(
            self.plans_dir,
            "my-chat",
            sample_recommendation(),
            sample_advice(),
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        advice = data["llm"]

        self.assertIsNotNone(advice, "the llm section should be filled when advice was saved")
        for key in ("summary", "risks", "suggestions", "confidence", "prompt_estimate", "usage"):
            self.assertIn(key, advice, f"the llm section should carry '{key}'")
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.assertIn(key, advice["usage"], f"the token usage should carry '{key}'")

    def test_the_llm_section_is_null_without_an_llm_pass(self):
        path = storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())
        data = json.loads(path.read_text(encoding="utf-8"))

        self.assertIsNone(data["llm"], "no LLM pass means a null llm section")


class TestPlanDict(unittest.TestCase):
    """Turning a plan into a dictionary and back again."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plans_dir = Path(self.tmp.name) / "plans"

    def test_a_plan_becomes_a_dictionary(self):
        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation(), sample_advice())
        plan = storage.load_plan(self.plans_dir, "my-chat")

        data = storage.plan_to_dict(plan)

        self.assertIsInstance(data, dict, "plan_to_dict should return a dictionary")
        for key in ("name", "saved_at", "profile", "recommendation", "llm"):
            self.assertIn(key, data, f"the dictionary should carry '{key}'")
        self.assertEqual(data["name"], "my-chat", "the dictionary should carry the plan name")

    def test_a_dictionary_becomes_a_plan_again(self):
        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation(), sample_advice())
        plan = storage.load_plan(self.plans_dir, "my-chat")

        again = storage.plan_from_dict(storage.plan_to_dict(plan))

        self.assertIsInstance(again, storage.SavedPlan, "plan_from_dict should return a SavedPlan")
        self.assertEqual(again.name, plan.name, "the name should survive the dictionary round trip")
        self.assertEqual(again.saved_at, plan.saved_at, "the saved time should survive the dictionary round trip")
        self.assertEqual(again.profile.goal, plan.profile.goal, "the goal should survive the dictionary round trip")
        self.assertAlmostEqual(
            again.recommendation.monthly_cost,
            plan.recommendation.monthly_cost,
            places=2,
            msg="the cost should survive the dictionary round trip",
        )
        self.assertEqual(
            len(again.recommendation.stack),
            len(plan.recommendation.stack),
            "the stack should survive the dictionary round trip",
        )


class TestLoadPlan(unittest.TestCase):
    """Reading a plan back and the errors that can happen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plans_dir = Path(self.tmp.name) / "plans"

    def test_a_missing_file_raises_a_plan_error(self):
        self.plans_dir.mkdir(parents=True, exist_ok=True)

        with self.assertRaises(storage.PlanError, msg="a missing plan file should raise PlanError"):
            storage.load_plan(self.plans_dir, "not-there")

    def test_a_file_that_is_not_json_raises_a_plan_error(self):
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        path = storage.plan_path(self.plans_dir, "broken")
        path.write_text("this is not json at all", encoding="utf-8")

        with self.assertRaises(storage.PlanError, msg="a file that is not JSON should raise PlanError"):
            storage.load_plan(self.plans_dir, "broken")

    def test_a_file_that_is_not_a_plan_raises_a_plan_error(self):
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        path = storage.plan_path(self.plans_dir, "empty")
        path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

        with self.assertRaises(storage.PlanError, msg="a JSON file that is not a plan should raise PlanError"):
            storage.load_plan(self.plans_dir, "empty")

    def test_a_newer_schema_version_raises_a_plan_error(self):
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        path = storage.plan_path(self.plans_dir, "from-the-future")
        path.write_text(
            json.dumps({"schema_version": 999, "name": "from-the-future"}),
            encoding="utf-8",
        )

        with self.assertRaises(storage.PlanError, msg="a newer schema version should raise PlanError"):
            storage.load_plan(self.plans_dir, "from-the-future")


class TestListPlans(unittest.TestCase):
    """The summaries of the saved plans."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plans_dir = Path(self.tmp.name) / "plans"

    def test_a_missing_folder_gives_an_empty_list(self):
        self.assertFalse(self.plans_dir.exists(), "the plans folder should not exist yet")

        summaries = storage.list_plans(self.plans_dir)

        self.assertEqual(summaries, [], "a missing folder has no plans")

    def test_every_saved_plan_gives_one_summary(self):
        storage.save_plan(self.plans_dir, "alpha", sample_recommendation())
        storage.save_plan(self.plans_dir, "beta", sample_recommendation())

        summaries = storage.list_plans(self.plans_dir)

        self.assertEqual(len(summaries), 2, "there should be one summary per saved plan")

    def test_the_summaries_are_sorted_by_name(self):
        for name in ("zebra", "alpha", "middle"):
            storage.save_plan(self.plans_dir, name, sample_recommendation())

        summaries = storage.list_plans(self.plans_dir)

        self.assertEqual(
            [summary.name for summary in summaries],
            ["alpha", "middle", "zebra"],
            "the summaries should be sorted by name",
        )

    def test_the_summary_carries_the_name_goal_cost_and_time(self):
        rec = sample_recommendation()
        path = storage.save_plan(self.plans_dir, "my-chat", rec)

        summaries = storage.list_plans(self.plans_dir)

        self.assertEqual(len(summaries), 1, "there should be one summary")
        summary = summaries[0]
        self.assertEqual(summary.name, "my-chat", "the summary should carry the plan name")
        self.assertEqual(summary.goal, "rag", "the summary should carry the goal")
        self.assertAlmostEqual(
            summary.monthly_cost,
            rec.monthly_cost,
            places=2,
            msg="the summary should carry the monthly cost",
        )
        self.assertTrue(summary.saved_at, "the summary should carry the saved time")

        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            summary.saved_at,
            saved["saved_at"],
            "the summary should carry the saved time of the plan",
        )


class TestDeletePlan(unittest.TestCase):
    """Removing a saved plan."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plans_dir = Path(self.tmp.name) / "plans"

    def test_the_file_is_removed(self):
        path = storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())
        self.assertTrue(path.exists(), "the plan should be on disk before it is deleted")

        storage.delete_plan(self.plans_dir, "my-chat")

        self.assertFalse(path.exists(), "the plan file should be gone after the delete")

    def test_deleting_twice_raises_a_plan_error(self):
        storage.save_plan(self.plans_dir, "my-chat", sample_recommendation())

        storage.delete_plan(self.plans_dir, "my-chat")

        with self.assertRaises(storage.PlanError, msg="deleting a plan twice should raise PlanError"):
            storage.delete_plan(self.plans_dir, "my-chat")

    def test_deleting_a_plan_that_does_not_exist_raises_a_plan_error(self):
        self.plans_dir.mkdir(parents=True, exist_ok=True)

        with self.assertRaises(storage.PlanError, msg="a missing plan should raise PlanError"):
            storage.delete_plan(self.plans_dir, "not-there")


class TestPlansDir(unittest.TestCase):
    """Where the plans folder lives."""

    def test_the_default_folder_is_called_plans(self):
        self.assertEqual(
            storage.default_plans_dir().name,
            "plans",
            "the default plans folder should be called 'plans'",
        )

    def test_the_default_folder_is_in_the_repository_root(self):
        parent = storage.default_plans_dir().parent

        self.assertEqual(
            parent.resolve(),
            REPO_ROOT.resolve(),
            "the default plans folder should live in the repository root",
        )
        self.assertTrue(
            (parent / "ai-tech-stack-planner").is_dir(),
            "the repository root is the folder that holds ai-tech-stack-planner",
        )

    def test_an_explicit_folder_is_used_as_it_is(self):
        chosen = storage.resolve_plans_dir("/tmp/elsewhere")

        self.assertEqual(
            chosen.resolve(),
            Path("/tmp/elsewhere").resolve(),
            "an explicit folder should be used as it is",
        )

    def test_without_an_argument_the_default_folder_is_used(self):
        self.assertEqual(
            storage.resolve_plans_dir().resolve(),
            storage.default_plans_dir().resolve(),
            "without an argument the default plans folder should be used",
        )


if __name__ == "__main__":
    unittest.main()
