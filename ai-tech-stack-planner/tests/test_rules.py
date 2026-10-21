"""Tests for the rule engine (ai-tech-stack-planner/src/rules.py).

Phase 1 is fully deterministic: no LLM, no tokens.  These tests therefore
pin down the exact boundaries of the budget tiers, the timeline buckets, the
base stacks and the behaviour of the rule engine itself.
"""

import sys
import unittest
from pathlib import Path

# Make the sibling src/ modules importable without installing anything.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rules import (  # noqa: E402  (import after the sys.path tweak)
    CONSTRAINTS,
    GOALS,
    TIERS,
    TIMELINE_BUCKETS,
    Component,
    Rule,
    apply_rules,
    base_stack,
    budget_tier,
    match_rules,
    timeline_bucket,
)

#: The goals the planner supports.
ALL_GOALS = ("chat", "rag", "document", "code")

#: The budget tiers the planner supports.
ALL_TIERS = ("micro", "mid", "enterprise")

#: The timeline buckets the planner supports.
ALL_BUCKETS = ("quick", "standard", "long")

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


class TestCatalogues(unittest.TestCase):
    """The lookup tables that drive the rest of the planner."""

    def test_goals_cover_the_documented_four(self):
        for goal in ALL_GOALS:
            self.assertIn(goal, GOALS)

    def test_constraints_cover_the_documented_six(self):
        for constraint in (
            "needs_api",
            "local_only",
            "enterprise",
            "scale",
            "existing_azure",
            "open_source",
        ):
            self.assertIn(constraint, CONSTRAINTS)

    def test_tiers_cover_the_three_budget_tiers(self):
        for tier in ALL_TIERS:
            self.assertIn(tier, TIERS)

    def test_timeline_buckets_cover_the_three_buckets(self):
        for bucket in ALL_BUCKETS:
            self.assertIn(bucket, TIMELINE_BUCKETS)


class TestBudgetTier(unittest.TestCase):
    """Budget -> tier: < $50 micro, <= $500 mid, above that enterprise."""

    def test_zero_budget_is_micro(self):
        self.assertEqual(budget_tier(0), "micro")

    def test_just_below_fifty_is_micro(self):
        self.assertEqual(budget_tier(49.99), "micro")

    def test_exactly_fifty_is_mid(self):
        self.assertEqual(budget_tier(50), "mid")

    def test_exactly_five_hundred_is_mid(self):
        self.assertEqual(budget_tier(500), "mid")

    def test_just_above_five_hundred_is_enterprise(self):
        self.assertEqual(budget_tier(500.01), "enterprise")

    def test_large_budget_is_enterprise(self):
        self.assertEqual(budget_tier(5000), "enterprise")

    def test_table_of_boundary_values(self):
        for budget, expected in (
            (0, "micro"),
            (49.99, "micro"),
            (50, "mid"),
            (500, "mid"),
            (500.01, "enterprise"),
            (5000, "enterprise"),
        ):
            with self.subTest(budget=budget):
                self.assertEqual(budget_tier(budget), expected)


class TestTimelineBucket(unittest.TestCase):
    """Timeline -> bucket: <= 4 weeks quick, <= 12 weeks standard, else long."""

    def test_one_week_is_quick(self):
        self.assertEqual(timeline_bucket(1), "quick")

    def test_exactly_four_weeks_is_quick(self):
        self.assertEqual(timeline_bucket(4), "quick")

    def test_five_weeks_is_standard(self):
        self.assertEqual(timeline_bucket(5), "standard")

    def test_exactly_twelve_weeks_is_standard(self):
        self.assertEqual(timeline_bucket(12), "standard")

    def test_thirteen_weeks_is_long(self):
        self.assertEqual(timeline_bucket(13), "long")

    def test_table_of_boundary_values(self):
        for weeks, expected in (
            (1, "quick"),
            (4, "quick"),
            (5, "standard"),
            (12, "standard"),
            (13, "long"),
        ):
            with self.subTest(weeks=weeks):
                self.assertEqual(timeline_bucket(weeks), expected)


class TestBaseStack(unittest.TestCase):
    """Every goal must produce a usable stack at every budget tier."""

    def test_every_goal_and_tier_produces_a_non_empty_stack(self):
        for goal in ALL_GOALS:
            for tier in ALL_TIERS:
                with self.subTest(goal=goal, tier=tier):
                    stack = base_stack(goal, tier)
                    self.assertTrue(stack, "base stack must not be empty")
                    for component in stack:
                        self.assertIsInstance(component, Component)
                        self.assertTrue(component.name.strip())

    def test_micro_chat_stack_uses_ollama(self):
        # Plan example 1: a small budget for a chat bot stays local.
        stack = base_stack("chat", "micro")
        self.assertIn("Ollama", names_of(stack))

    def test_micro_stack_has_a_free_component(self):
        # The local hosting piece of the micro stack must not cost anything.
        stack = base_stack("chat", "micro")
        free = [c for c in stack if c.cost_month == 0]
        self.assertTrue(free, "the micro stack should contain a free component")

    def test_rag_always_has_a_search_or_vector_component(self):
        for tier in ALL_TIERS:
            with self.subTest(tier=tier):
                stack = base_stack("rag", tier)
                self.assertTrue(
                    has_keyword(stack, GOAL_KEYWORDS["rag"]),
                    f"rag stack {names_of(stack)} has no search/vector component",
                )

    def test_document_always_has_a_document_component(self):
        for tier in ALL_TIERS:
            with self.subTest(tier=tier):
                stack = base_stack("document", tier)
                self.assertTrue(
                    has_keyword(stack, GOAL_KEYWORDS["document"]),
                    f"document stack {names_of(stack)} has no document component",
                )

    def test_code_always_has_a_code_assistant_component(self):
        for tier in ALL_TIERS:
            with self.subTest(tier=tier):
                stack = base_stack("code", tier)
                self.assertTrue(
                    has_keyword(stack, GOAL_KEYWORDS["code"]),
                    f"code stack {names_of(stack)} has no code assistant component",
                )


class TestMatchRules(unittest.TestCase):
    """Turning constraints into rules."""

    def test_no_constraints_matches_no_rules(self):
        self.assertEqual(list(match_rules([])), [])

    def test_needs_api_matches_at_least_one_rule(self):
        rules = list(match_rules(["needs_api"]))
        self.assertTrue(rules, "the needs_api constraint should match a rule")
        for rule in rules:
            self.assertIsInstance(rule, Rule)

    def test_local_only_matches_at_least_one_rule(self):
        rules = list(match_rules(["local_only"]))
        self.assertTrue(rules, "the local_only constraint should match a rule")

    def test_unknown_constraint_matches_no_rule(self):
        rules = list(match_rules(["definitely-not-a-constraint"]))
        self.assertEqual(rules, [])

    def test_every_matched_rule_has_the_documented_fields(self):
        for rule in match_rules(["needs_api", "local_only", "enterprise"]):
            self.assertTrue(rule.id.strip())
            self.assertIsInstance(rule.add, tuple)
            self.assertIsInstance(rule.remove, tuple)


class TestApplyRules(unittest.TestCase):
    """Applying rules to a stack: no surprises, no side effects."""

    def setUp(self):
        self.model = Component(
            name="Test Model",
            category="model",
            purpose="A model used by the tests",
            cost_month=0.0,
        )
        self.host = Component(
            name="Test Host",
            category="hosting",
            purpose="A host used by the tests",
            cost_month=0.0,
        )
        self.stack = [self.model, self.host]

    def test_the_given_list_is_not_mutated(self):
        snapshot = list(self.stack)
        rule = Rule(
            id="add",
            description="Adds an extra component",
            trigger="needs_api",
            add=(Component("Extra", "model", "extra", 5.0),),
            remove=(),
            note="n",
        )
        new_stack, applied = apply_rules(self.stack, [rule])

        self.assertEqual(self.stack, snapshot, "the input list was modified")
        self.assertEqual(len(self.stack), 2)
        self.assertEqual(len(new_stack), 3)
        self.assertEqual(len(applied), 1)

    def test_rule_that_changes_nothing_is_not_reported(self):
        noop = Rule(
            id="noop",
            description="Does nothing",
            trigger="nothing",
            add=(),
            remove=(),
            note="n",
        )
        new_stack, applied = apply_rules(self.stack, [noop])

        self.assertEqual(applied, [], "a no-op rule must not be reported")
        self.assertEqual(new_stack, self.stack)

    def test_adding_an_already_present_component_is_not_reported(self):
        duplicate = Rule(
            id="dup",
            description="Adds a component that is already there",
            trigger="needs_api",
            add=(self.model,),
            remove=(),
            note="n",
        )
        new_stack, applied = apply_rules(self.stack, [duplicate])

        self.assertEqual(applied, [], "a duplicate add must not be reported")
        self.assertEqual(len(new_stack), 2)

    def test_removing_an_absent_component_is_not_reported(self):
        missing = Rule(
            id="missing",
            description="Removes something that is not in the stack",
            trigger="local_only",
            add=(),
            remove=("Not In The Stack",),
            note="n",
        )
        new_stack, applied = apply_rules(self.stack, [missing])

        self.assertEqual(applied, [], "a no-op remove must not be reported")
        self.assertEqual(len(new_stack), 2)

    def test_adding_a_new_component_is_reported(self):
        extra = Component("Extra", "model", "extra", 5.0)
        add = Rule(
            id="add",
            description="Adds a component",
            trigger="needs_api",
            add=(extra,),
            remove=(),
            note="n",
        )
        new_stack, applied = apply_rules(self.stack, [add])

        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].id, "add")
        self.assertIn("Extra", names_of(new_stack))
        self.assertEqual(len(new_stack), 3)

    def test_removing_an_existing_component_is_reported(self):
        remove = Rule(
            id="remove",
            description="Removes a component",
            trigger="local_only",
            add=(),
            remove=("Test Host",),
            note="n",
        )
        new_stack, applied = apply_rules(self.stack, [remove])

        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].id, "remove")
        self.assertEqual(names_of(new_stack), ["Test Model"])

    def test_a_component_is_never_added_twice(self):
        extra = Component("Extra", "model", "extra", 5.0)
        rule = Rule(
            id="add",
            description="Adds a component",
            trigger="needs_api",
            add=(extra,),
            remove=(),
            note="n",
        )
        new_stack, _applied = apply_rules(self.stack, [rule, rule])

        self.assertEqual(names_of(new_stack).count("Extra"), 1)


if __name__ == "__main__":
    unittest.main()
