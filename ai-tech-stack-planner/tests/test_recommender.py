"""Tests for the stack recommender (ai-tech-stack-planner/src/recommender.py).

The recommender glues the rule engine together: it builds a stack, adds up
the monthly cost and writes the report that the CLI prints.
"""

import sys
import unittest
from pathlib import Path

# Make the sibling src/ modules importable without installing anything.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from recommender import (  # noqa: E402  (import after the sys.path tweak)
    ProjectProfile,
    Recommendation,
    format_recommendation,
    recommend,
)

#: The goals the planner supports.
ALL_GOALS = ("chat", "rag", "document", "code")

#: Budgets that hit the micro, mid and enterprise tiers.
ALL_BUDGETS = (30, 100, 2000)

#: For every goal, the keywords a matching component name must contain.
#: The checks stay case insensitive and keyword based so that small naming
#: changes do not break the tests.
GOAL_KEYWORDS = {
    "rag": ("search", "faiss", "chroma"),
    "document": ("document", "doc", "tesseract"),
    "code": ("copilot", "continue", "code"),
}


def names_of(stack):
    """Return the component names of a stack."""
    return [component.name for component in stack]


def has_keyword(stack, keywords):
    """True when any component name contains one of `keywords` (case insensitive)."""
    lowered = [component.name.lower() for component in stack]
    return any(keyword in name for name in lowered for keyword in keywords)


def cost_appears(text, cost):
    """True when the rendered report shows the total cost in a common format."""
    candidates = {
        f"{cost:.2f}",  # 30.00
        f"{cost:.1f}",  # 30.0
        f"{cost:g}",  # 30
        f"{cost:,.2f}",  # 1,234.50
    }
    return any(candidate in text for candidate in candidates)


class TestPlanExamples(unittest.TestCase):
    """The three example rules that plan.md promises."""

    def test_example_1_micro_budget_uses_ollama_and_free_local_hosting(self):
        # "Budget < $50 + simple chat -> open-source small model + local hosting"
        rec = recommend(
            ProjectProfile(
                goal="chat",
                budget=30,
                timeline_weeks=4,
                constraints=[],
            )
        )

        self.assertIn("Ollama", names_of(rec.stack), "expected an Ollama component")

        free = [c for c in rec.stack if c.cost_month == 0]
        self.assertTrue(free, "the micro stack should include a free local component")

        for component in rec.stack:
            if "local" in component.name.lower():
                self.assertEqual(component.cost_month, 0, "local hosting must be free")

    def test_example_2_mid_budget_with_api_needs_openai_and_functions(self):
        # "$50-$500 + needs API -> Azure OpenAI small model + serverless function"
        rec = recommend(
            ProjectProfile(
                goal="chat",
                budget=100,
                timeline_weeks=4,
                constraints=["needs_api"],
            )
        )

        self.assertTrue(
            has_keyword(rec.stack, ("openai",)),
            f"expected an Azure OpenAI component in {names_of(rec.stack)}",
        )
        self.assertTrue(
            has_keyword(rec.stack, ("functions",)),
            f"expected a serverless Functions component in {names_of(rec.stack)}",
        )

    def test_example_3_enterprise_budget_enterprise_constraint(self):
        # "> $500 + enterprise needs -> Azure OpenAI + Azure AI Search + App Service"
        rec = recommend(
            ProjectProfile(
                goal="rag",
                budget=2000,
                timeline_weeks=8,
                constraints=["enterprise"],
            )
        )

        self.assertTrue(
            has_keyword(rec.stack, ("openai",)),
            f"expected an Azure OpenAI component in {names_of(rec.stack)}",
        )
        self.assertTrue(
            has_keyword(rec.stack, ("search",)),
            f"expected an Azure AI Search component in {names_of(rec.stack)}",
        )
        self.assertTrue(
            has_keyword(rec.stack, ("app service",)),
            f"expected an App Service component in {names_of(rec.stack)}",
        )


class TestRecommend(unittest.TestCase):
    """The general behaviour of recommend()."""

    def test_monthly_cost_is_the_sum_of_the_stack(self):
        rec = recommend(
            ProjectProfile(
                goal="chat",
                budget=100,
                timeline_weeks=4,
                constraints=["needs_api"],
            )
        )

        expected = sum(component.cost_month for component in rec.stack)
        # places=2 because both sides are rounded to cents.
        self.assertAlmostEqual(rec.monthly_cost, expected, places=2)

    def test_notes_are_always_provided(self):
        rec = recommend(
            ProjectProfile(
                goal="chat",
                budget=100,
                timeline_weeks=4,
                constraints=[],
            )
        )

        self.assertTrue(rec.notes, "a recommendation should come with tips")
        for note in rec.notes:
            self.assertIsInstance(note, str)
            self.assertTrue(note.strip(), "tips must not be empty strings")

    def test_empty_constraints_are_supported(self):
        rec = recommend(
            ProjectProfile(
                goal="chat",
                budget=100,
                timeline_weeks=4,
                constraints=[],
            )
        )

        self.assertTrue(rec.stack, "an empty constraint list must still give a stack")
        self.assertIsInstance(rec.applied_rules, list)

    def test_unknown_goal_does_not_raise(self):
        rec = recommend(
            ProjectProfile(
                goal="not-a-real-goal",
                budget=100,
                timeline_weeks=4,
                constraints=[],
            )
        )

        self.assertIsInstance(rec, Recommendation)
        self.assertEqual(rec.profile.goal, "not-a-real-goal")
        expected = sum(component.cost_month for component in rec.stack)
        self.assertAlmostEqual(rec.monthly_cost, expected, places=2)

    def test_every_goal_produces_a_non_empty_stack(self):
        for goal in ALL_GOALS:
            for budget in ALL_BUDGETS:
                with self.subTest(goal=goal, budget=budget):
                    rec = recommend(
                        ProjectProfile(
                            goal=goal,
                            budget=budget,
                            timeline_weeks=4,
                            constraints=[],
                        )
                    )
                    self.assertTrue(rec.stack, "the stack must not be empty")

    def test_goal_specific_component_survives_the_rules(self):
        for goal, keywords in GOAL_KEYWORDS.items():
            for budget in ALL_BUDGETS:
                with self.subTest(goal=goal, budget=budget):
                    rec = recommend(
                        ProjectProfile(
                            goal=goal,
                            budget=budget,
                            timeline_weeks=4,
                            constraints=[],
                        )
                    )
                    self.assertTrue(
                        has_keyword(rec.stack, keywords),
                        f"{goal} stack {names_of(rec.stack)} is missing {keywords}",
                    )


class TestFormatRecommendation(unittest.TestCase):
    """The printed report."""

    def setUp(self):
        self.rec = recommend(
            ProjectProfile(
                goal="chat",
                budget=100,
                timeline_weeks=4,
                constraints=["needs_api"],
            )
        )
        self.text = format_recommendation(self.rec)

    def test_report_is_not_empty(self):
        self.assertTrue(self.text.strip())

    def test_report_mentions_every_component(self):
        for component in self.rec.stack:
            self.assertIn(component.name, self.text)

    def test_report_mentions_the_total_cost(self):
        self.assertTrue(
            cost_appears(self.text, self.rec.monthly_cost),
            f"total cost {self.rec.monthly_cost} missing from:\n{self.text}",
        )

    def test_report_has_a_tips_section(self):
        self.assertIn("Tips", self.text)


if __name__ == "__main__":
    unittest.main()
