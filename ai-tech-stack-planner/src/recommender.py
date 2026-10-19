"""Combine the rule engine's output into a full stack recommendation.

This module is the middle step of the Phase 1 pipeline::

    user answers  ->  rule engine (rules.py)  ->  this module  ->  report

The rule engine decides *what* to recommend.  This module works out the
*cost*, writes a few practical tips, and turns everything into a plain-text
report that is easy to read in a terminal.

Everything here is deterministic and uses only the Python standard library,
so no tokens are ever spent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import rules
from rules import (
    Component,
    Rule,
    apply_rules,
    base_stack,
    budget_tier,
    match_rules,
    timeline_bucket,
)

# Width of the separator lines used in the printed report.
WIDTH = 58
RULE = "-" * WIDTH
BAR = "=" * WIDTH


@dataclass
class ProjectProfile:
    """The answers the user gave us about their project."""

    goal: str
    budget: float
    timeline_weeks: int
    constraints: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    """A finished recommendation: the stack, its cost, and why."""

    profile: ProjectProfile
    stack: list[Component]
    monthly_cost: float
    applied_rules: list[Rule]
    notes: list[str]


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------


def format_amount(amount: float) -> str:
    """Format dollars for people: ``$0``, ``$5`` or ``$12.50``."""
    rounded = round(amount, 2)
    if rounded == int(rounded):
        return f"${int(rounded)}"
    return f"${rounded:.2f}"


def format_cost(amount: float) -> str:
    """Format a monthly cost, for example ``$0/mo`` or ``$12.50/mo``."""
    return f"{format_amount(amount)}/mo"


def budget_verdict(monthly_cost: float, budget: float) -> str:
    """Say whether the stack fits inside the budget."""
    if monthly_cost <= budget:
        return "under budget"
    return "over budget — trim or phase"


def constraint_text(constraints: list[str]) -> str:
    """Turn constraint keys into readable labels, or ``none``."""
    if not constraints:
        return "none"
    labels = [rules.CONSTRAINTS.get(key, key) for key in constraints]
    return ", ".join(labels)


# ---------------------------------------------------------------------------
# Tips / notes
# ---------------------------------------------------------------------------


def budget_note(profile: ProjectProfile, monthly_cost: float) -> str:
    """The tip that compares the estimated cost with the stated budget."""
    verdict = budget_verdict(monthly_cost, profile.budget)
    return (
        f"Estimated {format_cost(monthly_cost)} vs your "
        f"{format_amount(profile.budget)}/mo budget — {verdict}."
    )


def timeline_note(profile: ProjectProfile) -> str:
    """The tip that matches the tip to how much time the user has."""
    bucket = timeline_bucket(profile.timeline_weeks)
    if bucket == "quick":
        return (
            "Timeline is tight — favour ready-made services over "
            "custom training."
        )
    if bucket == "long":
        return (
            "Timeline is long — you can afford a more "
            "custom/self-hosted setup."
        )
    return (
        "Timeline is standard — managed services with room to tune "
        "fit well."
    )


def rule_note(rule: Rule) -> str:
    """The tip for a single applied rule (never an empty string)."""
    for candidate in (rule.note, rule.description, rule.id):
        text = (candidate or "").strip()
        if text:
            return text
    return "An extra constraint was applied."


def unique_notes(notes: list[str]) -> list[str]:
    """Drop empty and repeated notes, keeping the original order."""
    seen: set[str] = set()
    result: list[str] = []
    for note in notes:
        text = note.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def build_notes(
    profile: ProjectProfile,
    monthly_cost: float,
    applied: list[Rule],
) -> list[str]:
    """Build the list of tips shown under the "Tips" section."""
    notes: list[str] = [
        budget_note(profile, monthly_cost),
        timeline_note(profile),
        "Start small and scale later: ship the smallest stack that proves "
        "the idea, then upgrade as usage grows.",
    ]

    if profile.goal not in rules.GOALS:
        notes.append(
            f"Goal '{profile.goal}' is not one of the known goals — "
            "this is a general starting stack."
        )

    unknown = [
        key
        for key in profile.constraints
        if key.strip().lower() not in {k.strip().lower() for k in rules.CONSTRAINTS}
    ]
    if unknown:
        notes.append(
            "Constraint(s) not in the known list: "
            f"{', '.join(unknown)} — they were ignored."
        )

    for rule in applied:
        notes.append(rule_note(rule))

    return unique_notes(notes)


# ---------------------------------------------------------------------------
# The recommendation itself
# ---------------------------------------------------------------------------


def recommend(profile: ProjectProfile) -> Recommendation:
    """Build a full stack recommendation for one project profile.

    The rule engine does the thinking: it gives us a base stack for the
    goal and budget tier, then applies the rules for the constraints the
    user chose.  We only add up the cost and write the tips.
    """
    tier = budget_tier(profile.budget)
    stack = base_stack(profile.goal, tier)

    # The tier and the goal are hints for the rules, so a rule can adapt to
    # the project.  For example the "local_only" rule only adds document
    # tools (Docling, Tesseract) for document projects, and the "enterprise"
    # rule only adds the hosted search and app service outside the micro
    # tier.  rules.match_rules still works with the constraints alone.
    matched = match_rules(profile.constraints, tier=tier, goal=profile.goal)
    stack, applied = apply_rules(stack, matched)

    monthly_cost = round(sum(component.cost_month for component in stack), 2)

    return Recommendation(
        profile=profile,
        stack=stack,
        monthly_cost=monthly_cost,
        applied_rules=applied,
        notes=build_notes(profile, monthly_cost, applied),
    )


# ---------------------------------------------------------------------------
# The printed report
# ---------------------------------------------------------------------------


def format_recommendation(rec: Recommendation) -> str:
    """Turn a recommendation into a plain-text report for the terminal."""
    profile = rec.profile
    tier = budget_tier(profile.budget)
    bucket = timeline_bucket(profile.timeline_weeks)

    goal_label = rules.GOALS.get(profile.goal, profile.goal)
    tier_label = rules.TIERS.get(tier, tier)
    bucket_label = rules.TIMELINE_BUCKETS.get(bucket, bucket)

    lines: list[str] = []
    lines.append(BAR)
    lines.append(f"AI Tech Stack Recommendation — {goal_label} ({tier_label})")
    lines.append("")

    # 1. Profile
    lines.append("Profile")
    lines.append(RULE)
    lines.append(f"Goal:        {goal_label}")
    lines.append(f"Budget:      {format_amount(profile.budget)}/mo")
    lines.append(f"Timeline:    {profile.timeline_weeks} weeks ({bucket_label})")
    lines.append(f"Constraints: {constraint_text(profile.constraints)}")
    lines.append("")

    # 2. Recommended stack
    lines.append("Recommended stack")
    lines.append(RULE)
    if rec.stack:
        for component in rec.stack:
            lines.append(
                f"  - {component.name} [{component.category}] — "
                f"{component.purpose} (~{format_cost(component.cost_month)})"
            )
    else:
        goals = ", ".join(sorted(rules.GOALS)) or "chat, rag, document, code"
        lines.append(f"  - (nothing matched — try a goal like {goals})")
    lines.append("")

    # 3. Cost
    verdict = budget_verdict(rec.monthly_cost, profile.budget)
    lines.append(
        f"Estimated monthly cost: {format_cost(rec.monthly_cost)} vs "
        f"{format_amount(profile.budget)}/mo budget — {verdict}"
    )
    lines.append("")

    # 4. Why this stack
    lines.append("Why this stack")
    lines.append(RULE)
    if rec.applied_rules:
        for rule in rec.applied_rules:
            description = (rule.description or "").strip()
            note = (rule.note or "").strip()
            if note:
                lines.append(f"  - {description} ({note})")
            else:
                lines.append(f"  - {description}")
    else:
        lines.append("  - No extra constraints were applied.")
    lines.append("")

    # 5. Tips
    lines.append("Tips")
    lines.append(RULE)
    for note in rec.notes:
        lines.append(f"  - {note}")
    lines.append(BAR)

    return "\n".join(lines)
