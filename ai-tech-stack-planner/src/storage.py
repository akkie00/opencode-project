"""Save and load project plans for the AI Tech Stack Planner (Phase 3).

Where this module sits in the pipeline::

    user input (goal, budget, timeline, constraints)
        -> rules.py + recommender.py  -> the recommended stack (always, free)
        -> llm.py                     -> extra reasoning about that stack (optional)
        -> storage.py                 -> the plan written to disk as one JSON file
        -> report

One plan is one JSON file inside the ``plans/`` folder of the repository.
One file per plan (instead of one big file for everything) means a plan can
be read, copied, diffed or deleted on its own, and a single broken file never
takes the other plans down with it.

The format is deliberately boring: plain JSON with a ``schema_version`` at the
top.  When the layout changes we bump that number, so a file written by a
newer version of the tool is refused with a clear message instead of being
read into nonsense.

Two things are deliberately *not* saved:

* the whole API response (``LLMAdvice.raw``) - it is bulky and nothing needs
  it to show a plan again
* the ``add`` and ``remove`` tuples of a rule - they are only needed to
  *apply* a rule, never to show a saved plan, so they are rebuilt as empty
  tuples when a plan is read back

Everything here uses only the Python standard library, and nothing in this
module prints anything: it returns data and the CLI prints it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from llm import LLMAdvice, TokenUsage
from recommender import ProjectProfile, Recommendation
from rules import Component, Rule

#: The version of the file format. Bump it when the layout changes.
SCHEMA_VERSION = 1

#: A plan name may only use letters, digits, dash and underscore.  The first
#: character cannot be a dash, so a name can never look like a command line
#: option, and the length limit keeps the file name short.  The pattern is
#: anchored with \A and \Z (not ^ and $) so that a name cannot sneak a
#: newline or a path separator past the check.
_NAME_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\Z")


class PlanError(Exception):
    """Raised when a plan cannot be found, read, written or deleted."""


@dataclass
class SavedPlan:
    """One plan that was written to disk."""

    name: str
    saved_at: str  # ISO 8601, e.g. "2026-10-20T12:34:56"
    profile: ProjectProfile
    recommendation: Recommendation
    llm: LLMAdvice | None = None


@dataclass
class PlanSummary:
    """The short description shown by --list-plans."""

    name: str
    goal: str
    monthly_cost: float
    saved_at: str


# ---------------------------------------------------------------------------
# Small conversion helpers
#
# A file can be edited by hand, so a value inside it might be missing or of
# the wrong type.  These helpers turn whatever we find into the type the rest
# of the code expects, with a safe fallback instead of an exception.
# ---------------------------------------------------------------------------


def _as_str(value: Any, default: str = "") -> str:
    """Return the value as text (the default when it is missing)."""
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    """Return the value as a number (the default when it is missing or broken).

    A number written as an int comes back as an int from JSON, so every
    number is converted with float() to keep the types consistent.
    """
    if isinstance(value, bool):  # True/False are not numbers we want here
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _as_int(value: Any, default: int = 0) -> int:
    """Return the value as a whole number (the default when it is broken)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _as_str_list(value: Any) -> list[str]:
    """Return the value as a list of strings (an empty list when it is not a list)."""
    if not isinstance(value, list):
        return []
    return [_as_str(item) for item in value]


def _as_dict(value: Any) -> dict:
    """Return the value when it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Where the plans live
# ---------------------------------------------------------------------------


def default_plans_dir() -> Path:
    """The plans folder: <repository root>/plans.

    The repository root is two levels above this file
    (src/ -> ai-tech-stack-planner/ -> repository root).
    """
    return Path(__file__).resolve().parents[2] / "plans"


def resolve_plans_dir(explicit: str | None = None) -> Path:
    """The plans directory to use: `explicit` when given, otherwise default_plans_dir().

    An empty or missing value means "use the default", so a blank command
    line option never points the planner at an odd place.  A leading "~" is
    expanded to the home folder, which is what a typed path usually means.
    """
    if explicit is None:
        return default_plans_dir()

    text = str(explicit).strip()
    if not text:
        return default_plans_dir()

    return Path(text).expanduser()


def valid_plan_name(name: str) -> bool:
    """True when `name` is a safe file name.

    Letters, digits, dash and underscore only, 1 to 64 characters, and it must
    not start with a dash. This keeps a plan name from turning into a path.
    """
    if not isinstance(name, str):
        return False
    return bool(_NAME_RE.match(name))


def plan_path(plans_dir: Path, name: str) -> Path:
    """The file that holds the plan.

    Raises PlanError when the name is not a valid plan name.
    """
    if not valid_plan_name(name):
        raise PlanError(
            f"{name!r} is not a valid plan name - use letters, digits, dash or "
            "underscore (1 to 64 characters, not starting with a dash)."
        )
    return Path(plans_dir) / f"{name}.json"


# ---------------------------------------------------------------------------
# Turning a plan into JSON and back
# ---------------------------------------------------------------------------


def _component_to_dict(component: Component) -> dict:
    """One stack component as a plain dict."""
    return {
        "name": component.name,
        "category": component.category,
        "purpose": component.purpose,
        "cost_month": float(component.cost_month),
    }


def _rule_to_dict(rule: Rule) -> dict:
    """One applied rule as a plain dict.

    The `add` and `remove` tuples are left out: they are only needed to
    apply a rule, never to show a saved plan.
    """
    return {
        "id": rule.id,
        "description": rule.description,
        "trigger": rule.trigger,
        "note": rule.note,
    }


def _llm_to_dict(advice: LLMAdvice | None) -> dict | None:
    """The LLM advice as a plain dict (None when the plan has no advice).

    The whole API response (`raw`) is left out on purpose - it is bulky and
    nothing needs it to show the plan again.
    """
    if advice is None:
        return None

    usage = getattr(advice, "usage", None) or TokenUsage()
    return {
        "summary": _as_str(advice.summary),
        "risks": list(advice.risks or []),
        "suggestions": list(advice.suggestions or []),
        "confidence": _as_str(advice.confidence),
        "prompt_estimate": int(advice.prompt_estimate),
        "usage": {
            "prompt_tokens": int(usage.prompt_tokens),
            "completion_tokens": int(usage.completion_tokens),
            "total_tokens": int(usage.total_tokens),
        },
    }


def plan_to_dict(plan: SavedPlan) -> dict:
    """Turn a plan into the plain dict that is written as JSON."""
    profile = plan.profile
    rec = plan.recommendation

    return {
        "schema_version": SCHEMA_VERSION,
        "name": _as_str(plan.name),
        "saved_at": _as_str(plan.saved_at),
        "profile": {
            "goal": _as_str(profile.goal),
            "budget": _as_float(profile.budget),
            "timeline_weeks": int(profile.timeline_weeks),
            "constraints": list(profile.constraints or []),
        },
        "recommendation": {
            "monthly_cost": float(rec.monthly_cost),
            "stack": [_component_to_dict(component) for component in rec.stack],
            "applied_rules": [_rule_to_dict(rule) for rule in rec.applied_rules],
            "notes": list(rec.notes or []),
        },
        "llm": _llm_to_dict(plan.llm),
    }


def _component_from_dict(data: dict) -> Component:
    """One stack component read back from a dict."""
    return Component(
        name=_as_str(data.get("name")),
        category=_as_str(data.get("category")),
        purpose=_as_str(data.get("purpose")),
        cost_month=_as_float(data.get("cost_month")),
    )


def _rule_from_dict(data: dict) -> Rule:
    """One applied rule read back from a dict.

    The `add` and `remove` tuples are rebuilt as empty ones - they were never
    saved, because they are only needed to apply a rule.
    """
    return Rule(
        id=_as_str(data.get("id")),
        description=_as_str(data.get("description")),
        trigger=_as_str(data.get("trigger")),
        add=(),
        remove=(),
        note=_as_str(data.get("note")),
    )


def _llm_from_dict(data: Any) -> LLMAdvice | None:
    """The LLM advice read back from a dict (None when there is none).

    A missing "llm" key means the plan was saved without an LLM pass, which
    is not an error - the plan simply has no advice attached.
    """
    if not isinstance(data, dict):
        return None

    usage = _as_dict(data.get("usage"))
    return LLMAdvice(
        summary=_as_str(data.get("summary")),
        risks=_as_str_list(data.get("risks")),
        suggestions=_as_str_list(data.get("suggestions")),
        confidence=_as_str(data.get("confidence")),
        usage=TokenUsage(
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            total_tokens=_as_int(usage.get("total_tokens")),
        ),
        prompt_estimate=_as_int(data.get("prompt_estimate")),
        raw={},
    )


def plan_from_dict(data: dict) -> SavedPlan:
    """Turn a dict read from a file back into a plan.

    Raises PlanError when the data is not a plan this version understands.
    """
    if not isinstance(data, dict):
        raise PlanError("the file is not a saved plan")

    if "schema_version" not in data:
        # No version at all means this is simply not one of our plan files.
        raise PlanError("the file is not a saved plan - it has no schema_version")

    version = data.get("schema_version")
    version_number = _as_int(version, default=-1)
    if version_number < 0:
        raise PlanError(
            f"the file is not a saved plan - its schema_version is unreadable "
            f"({version!r})"
        )
    if version_number > SCHEMA_VERSION:
        raise PlanError(
            f"the file was written by a newer version of the tool (schema "
            f"version {version_number}, this tool reads version {SCHEMA_VERSION})"
        )

    raw_profile = data.get("profile")
    if not isinstance(raw_profile, dict):
        raise PlanError("the file is not a saved plan - it has no usable profile")

    profile = ProjectProfile(
        goal=_as_str(raw_profile.get("goal")),
        budget=_as_float(raw_profile.get("budget")),
        timeline_weeks=_as_int(raw_profile.get("timeline_weeks")),
        constraints=_as_str_list(raw_profile.get("constraints")),
    )

    raw_rec = _as_dict(data.get("recommendation"))
    raw_stack = raw_rec.get("stack")
    raw_rules = raw_rec.get("applied_rules")

    rec = Recommendation(
        profile=profile,
        stack=[
            _component_from_dict(item)
            for item in (raw_stack if isinstance(raw_stack, list) else [])
            if isinstance(item, dict)
        ],
        monthly_cost=_as_float(raw_rec.get("monthly_cost")),
        applied_rules=[
            _rule_from_dict(item)
            for item in (raw_rules if isinstance(raw_rules, list) else [])
            if isinstance(item, dict)
        ],
        notes=_as_str_list(raw_rec.get("notes")),
    )

    return SavedPlan(
        name=_as_str(data.get("name")),
        saved_at=_as_str(data.get("saved_at")),
        profile=profile,
        recommendation=rec,
        llm=_llm_from_dict(data.get("llm")),
    )


# ---------------------------------------------------------------------------
# Saving, loading, listing and deleting
# ---------------------------------------------------------------------------


def save_plan(
    plans_dir: Path,
    name: str,
    rec: Recommendation,
    llm: LLMAdvice | None = None,
) -> Path:
    """Write one plan as one JSON file and return the path that was written.

    The folder is created when it does not exist yet. An existing plan with the
    same name is overwritten. Raises PlanError when the name is not valid or
    the file cannot be written.
    """
    # plan_path checks the name, so a bad name fails before anything is written.
    path = plan_path(plans_dir, name)

    plan = SavedPlan(
        name=name,
        # The local time, without a timezone, in a format people can read.
        saved_at=datetime.now().isoformat(timespec="seconds"),
        profile=rec.profile,
        recommendation=rec,
        llm=llm,
    )
    data = plan_to_dict(plan)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PlanError(f"could not create the plans folder {path.parent}: {exc}") from exc

    try:
        # indent=2 and ensure_ascii=False keep the file readable: the notes
        # keep their real dashes instead of \uXXXX escapes.  The trailing
        # newline means the file ends nicely in a text editor.
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise PlanError(f"could not write the plan file {path}: {exc}") from exc

    return path


def load_plan(plans_dir: Path, name: str) -> SavedPlan:
    """Read one saved plan.

    Raises PlanError when the plan does not exist, the file is not valid JSON,
    or the file was written by a newer version of the tool.
    """
    path = plan_path(plans_dir, name)

    if not path.exists():
        raise PlanError(f"there is no saved plan called {name!r} in {path.parent}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"could not read the plan file {path}: {exc}") from exc

    try:
        data = json.loads(text)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise PlanError(f"the plan file {path} is not valid JSON: {exc}") from exc

    return plan_from_dict(data)


def _summary_from_path(path: Path) -> PlanSummary | None:
    """The summary of one plan file, or None when the file cannot be used.

    A file that is broken, empty or was written by another tool is skipped
    instead of breaking the whole listing.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    profile = _as_dict(data.get("profile"))
    rec = _as_dict(data.get("recommendation"))

    return PlanSummary(
        # The name inside the file is the plan's name; the file name is the
        # fallback when the file was written without one.
        name=_as_str(data.get("name")) or path.stem,
        goal=_as_str(profile.get("goal")),
        monthly_cost=_as_float(rec.get("monthly_cost")),
        saved_at=_as_str(data.get("saved_at")),
    )


def list_plans(plans_dir: Path) -> list[PlanSummary]:
    """A summary of every saved plan, sorted by name.

    A folder that does not exist gives an empty list. A file that cannot be
    read is skipped rather than breaking the whole listing.
    """
    directory = Path(plans_dir)
    if not directory.is_dir():
        return []

    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return []

    summaries: list[PlanSummary] = []
    for path in files:
        summary = _summary_from_path(path)
        if summary is not None:
            summaries.append(summary)

    # Sorted without regard to upper/lower case, so the list reads the way a
    # human would expect (and a second key keeps the order stable).
    summaries.sort(key=lambda summary: (summary.name.lower(), summary.name))
    return summaries


def delete_plan(plans_dir: Path, name: str) -> None:
    """Delete one saved plan. Raises PlanError when it does not exist."""
    path = plan_path(plans_dir, name)

    if not path.exists():
        raise PlanError(f"there is no saved plan called {name!r} in {path.parent}")

    try:
        path.unlink()
    except OSError as exc:
        raise PlanError(f"could not delete the plan file {path}: {exc}") from exc
