"""The web interface for the AI Tech Stack Planner (Phase 4).

Where this module sits in the pipeline::

    browser (templates/index.html + static/app.js)
        -> this module        -> the HTTP layer and the JSON shapes
        -> rules.py + recommender.py  -> the recommended stack (always, free)
        -> llm.py             -> extra reasoning about that stack (optional)
        -> pricing.py         -> the live Azure prices (optional)
        -> storage.py         -> the plans written to disk

This module is a *thin adapter*.  It never decides anything itself: every
recommendation comes from ``recommender.recommend()``, every rule from
``rules.py``, every LLM reply from ``llm.call_llm()`` and every file from
``storage.py``.  The only new code here is the HTTP layer, the JSON shapes
and the checking of what the browser sends.

Two rules shape the behaviour:

* the LLM must never break the planner - a missing key or a failed call is
  reported in ``llm_error`` and the rule based answer is still returned
* the app is local by default - it binds to 127.0.0.1, so nothing on the
  network can reach it

Flask is the only dependency of the whole project and it is needed for this
module only: the command line planner keeps working without it.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

import rules
import storage
from llm import LLMConfig, LLMError, call_llm
from main import DEFAULT_BUDGET, DEFAULT_GOAL, DEFAULT_TIMELINE
from recommender import ProjectProfile, build_notes, recommend

try:  # the live price list is optional - see the note in _apply_pricing()
    import pricing
except ImportError:  # pragma: no cover - only when pricing.py is missing
    pricing = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

#: The folder with the templates and the static files (one level above src/).
BASE_DIR = Path(__file__).resolve().parents[1]

#: The Azure region the live prices are read for.
DEFAULT_REGION = "eastus"

#: The host the app binds to: localhost only, so nothing on the network can
#: reach the planner.
DEFAULT_HOST = "127.0.0.1"

#: The port the app listens on.
DEFAULT_PORT = 8000

#: The two price modes the API reports in the "pricing" field.
PRICING_LIVE = "live"
PRICING_ESTIMATE = "estimate"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """True when the value is a real number (a bool is not a number here)."""
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float))


def _as_bool(value: Any, default: bool = False) -> bool:
    """Read a checkbox value from the JSON body.

    ``true``/``1``/``"yes"``/``"on"`` mean True, ``false``/``0``/``"no"``/
    ``"off"`` mean False and a missing value means `default`.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if _is_number(value):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
    return default


def _as_number(value: Any, default: float, name: str) -> float:
    """Read a number from the JSON body, or raise ValueError with a message."""
    if value is None:
        return float(default)
    if isinstance(value, bool):
        raise ValueError(f"The {name} must be a number, not {value!r}.")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise ValueError(f"The {name} must be a number, not {value!r}.") from None
    else:
        raise ValueError(f"The {name} must be a number, not {value!r}.")

    if not math.isfinite(number):
        raise ValueError(f"The {name} must be a real number, not {value!r}.")
    return number


def _as_whole_number(value: Any, default: int, name: str) -> int:
    """Read a whole number from the JSON body, or raise ValueError."""
    if value is None:
        return int(default)
    if isinstance(value, bool):
        raise ValueError(f"The {name} must be a whole number, not {value!r}.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"The {name} must be a whole number, not {value!r}.")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            pass
        try:
            number = float(text)
        except ValueError:
            raise ValueError(
                f"The {name} must be a whole number, not {value!r}."
            ) from None
        if number.is_integer():
            return int(number)
        raise ValueError(f"The {name} must be a whole number, not {value!r}.")
    raise ValueError(f"The {name} must be a whole number, not {value!r}.")


def _error(message: str, status: int):
    """The one error shape of the API: {"error": "a message you can act on"}."""
    return jsonify({"error": message}), status


# ---------------------------------------------------------------------------
# JSON shapes
# ---------------------------------------------------------------------------


def _profile_json(profile: ProjectProfile) -> dict:
    """The project profile as the API sends it."""
    return {
        "goal": profile.goal,
        "budget": float(profile.budget),
        "timeline_weeks": int(profile.timeline_weeks),
        "constraints": list(profile.constraints),
    }


def _component_json(component) -> dict:
    """One stack component as the API sends it."""
    return {
        "name": component.name,
        "category": component.category,
        "purpose": component.purpose,
        "cost_month": float(component.cost_month),
    }


def _rule_json(rule) -> dict:
    """One applied rule as the API sends it."""
    return {
        "id": rule.id,
        "description": rule.description,
        "note": rule.note,
    }


def _advice_json(advice) -> dict | None:
    """The LLM advice as the API sends it (None when there is no advice)."""
    if advice is None:
        return None

    usage = getattr(advice, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)

    return {
        "summary": advice.summary or "",
        "risks": list(advice.risks or []),
        "suggestions": list(advice.suggestions or []),
        "confidence": advice.confidence or "",
        "prompt_estimate": int(advice.prompt_estimate or 0),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def _saved_plan_json(plan: storage.SavedPlan) -> dict:
    """One saved plan as the API sends it."""
    rec = plan.recommendation
    return {
        "name": plan.name,
        "saved_at": plan.saved_at,
        "profile": _profile_json(plan.profile),
        "monthly_cost": float(rec.monthly_cost),
        "stack": [_component_json(component) for component in rec.stack],
        "applied_rules": [_rule_json(rule) for rule in rec.applied_rules],
        "notes": list(rec.notes or []),
        "llm": _advice_json(plan.llm),
    }


def _plan_summaries(plans_dir: Path) -> list[dict]:
    """The short list of saved plans."""
    return [
        {
            "name": summary.name,
            "goal": summary.goal,
            "monthly_cost": float(summary.monthly_cost),
            "saved_at": summary.saved_at,
        }
        for summary in storage.list_plans(plans_dir)
    ]


# ---------------------------------------------------------------------------
# Reading the request
# ---------------------------------------------------------------------------


def _profile_from_body(body: dict) -> ProjectProfile:
    """Build a ProjectProfile from the JSON body.

    Raises ValueError with a message a person can act on when something is
    wrong.  A value that is not given at all falls back to the same default
    the command line uses.
    """
    raw_goal = body.get("goal")
    if raw_goal is None or (isinstance(raw_goal, str) and not raw_goal.strip()):
        goal = DEFAULT_GOAL
    elif not isinstance(raw_goal, str):
        raise ValueError(
            f"The goal must be one of: {', '.join(rules.GOALS)}."
        )
    else:
        goal = raw_goal.strip().lower()
        if goal not in rules.GOALS:
            raise ValueError(
                f"Unknown goal {raw_goal!r}. Valid goals: {', '.join(rules.GOALS)}."
            )

    budget = _as_number(body.get("budget"), DEFAULT_BUDGET, "budget")
    if budget < 0:
        raise ValueError(f"The budget must be 0 or more, not {budget:g}.")

    weeks = _as_whole_number(body.get("timeline_weeks"), DEFAULT_TIMELINE, "timeline_weeks")
    if weeks < 1:
        raise ValueError(f"The timeline must be 1 week or more, not {weeks}.")

    raw_constraints = body.get("constraints")
    if raw_constraints is None:
        raw_constraints = []
    if not isinstance(raw_constraints, list):
        raise ValueError(
            "The constraints must be a list of constraint names, "
            f"for example {', '.join(rules.CONSTRAINTS)}."
        )

    constraints: list[str] = []
    for entry in raw_constraints:
        if not isinstance(entry, str):
            raise ValueError(
                f"Unknown constraint {entry!r}. "
                f"Valid constraints: {', '.join(rules.CONSTRAINTS)}."
            )
        key = entry.strip().lower()
        if key not in rules.CONSTRAINTS:
            raise ValueError(
                f"Unknown constraint {entry!r}. "
                f"Valid constraints: {', '.join(rules.CONSTRAINTS)}."
            )
        if key not in constraints:  # keep the order, drop duplicates
            constraints.append(key)

    return ProjectProfile(
        goal=goal,
        budget=budget,
        timeline_weeks=weeks,
        constraints=constraints,
    )


def _save_name_from_body(body: dict) -> str:
    """The plan name to save under ("" when the plan is not saved)."""
    raw_name = body.get("save_as")
    if raw_name is None:
        return ""
    if not isinstance(raw_name, str):
        raise ValueError(
            "The plan name must be text, for example 'my-chat'."
        )
    name = raw_name.strip()
    if not name:
        return ""
    if not storage.valid_plan_name(name):
        raise ValueError(
            f"{raw_name!r} is not a valid plan name - use letters, digits, dash "
            "or underscore (1 to 64 characters, not starting with a dash)."
        )
    return name


# ---------------------------------------------------------------------------
# The optional pieces
# ---------------------------------------------------------------------------


def _apply_pricing(stack, enabled: bool, region: str):
    """Replace the rough costs with the live Azure prices when they are there.

    Returns ``(stack, mode)`` where mode is "live" or "estimate".  The live
    price list is optional and it never breaks the planner: when it cannot be
    read the rough estimates are kept and the mode stays "estimate".
    """
    current = list(stack)
    if not enabled or pricing is None:
        return current, PRICING_ESTIMATE

    try:
        quotes = pricing.get_quotes(region=region)
    except Exception:
        # A broken price list must never break the planner.
        return current, PRICING_ESTIMATE

    try:
        priced, mode = pricing.apply_quotes(current, quotes)
    except Exception:
        return current, PRICING_ESTIMATE

    if mode not in (PRICING_LIVE, PRICING_ESTIMATE):
        mode = PRICING_ESTIMATE
    return list(priced), mode


def _run_llm(profile: ProjectProfile, stack, transport):
    """Run the optional LLM pass.

    Returns ``(advice, error)``: exactly one of the two is set.  A missing
    API key and a failed call are both reported as an error, so the caller
    can still send the rule based recommendation.
    """
    config = LLMConfig.from_env()

    if not (config.api_key or "").strip():
        return None, (
            "No API key set (LLM_API_KEY), so the LLM pass was skipped - "
            "the rule based recommendation below is unchanged."
        )

    try:
        return call_llm(config, profile, stack, transport=transport), ""
    except LLMError as exc:
        return None, f"{exc} - the rule based recommendation is unchanged."
    except Exception as exc:  # a broken LLM must never break the planner
        return None, (
            f"The request to the model failed: {exc} - "
            "the rule based recommendation is unchanged."
        )


# ---------------------------------------------------------------------------
# The Flask app
# ---------------------------------------------------------------------------


def create_app(
    plans_dir: Path | None = None,
    pricing_enabled: bool = True,
    pricing_region: str = DEFAULT_REGION,
    llm_transport=None,
) -> Flask:
    """Build the Flask app.

    plans_dir       - where the saved plans live (default: storage.default_plans_dir())
    pricing_enabled - turn the live Azure prices on or off
    pricing_region  - the Azure region to price in
    llm_transport   - injected into llm.call_llm, so the tests can use a fake
    """
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )

    app.config["PLANS_DIR"] = (
        Path(plans_dir) if plans_dir is not None else storage.default_plans_dir()
    )
    app.config["PRICING_ENABLED"] = bool(pricing_enabled)
    app.config["PRICING_REGION"] = str(pricing_region or DEFAULT_REGION)
    app.config["LLM_TRANSPORT"] = llm_transport

    def plans_folder() -> Path:
        """The folder the saved plans live in."""
        return Path(app.config["PLANS_DIR"])

    def transport():
        """The transport handed to llm.call_llm (None means the real one)."""
        return app.config["LLM_TRANSPORT"]

    # --- The page -----------------------------------------------------------

    @app.get("/")
    def index():
        """The one page of the app."""
        return render_template(
            "index.html",
            pricing_enabled=app.config["PRICING_ENABLED"],
            pricing_region=app.config["PRICING_REGION"],
        )

    # --- What the form needs ------------------------------------------------

    @app.get("/api/options")
    def options():
        """The goals, the constraints and the defaults for the form."""
        return jsonify(
            {
                "goals": dict(rules.GOALS),
                "constraints": dict(rules.CONSTRAINTS),
                "defaults": {
                    "goal": DEFAULT_GOAL,
                    "budget": float(DEFAULT_BUDGET),
                    "timeline_weeks": int(DEFAULT_TIMELINE),
                },
            }
        )

    # --- Plan a project -----------------------------------------------------

    @app.post("/api/plan")
    def plan():
        """Plan a project and (optionally) save the plan."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error(
                "Send a JSON object with a goal, a budget, a timeline and the "
                "constraints, for example "
                '{"goal": "chat", "budget": 100, "timeline_weeks": 4}.',
                400,
            )

        try:
            profile = _profile_from_body(body)
            save_as = _save_name_from_body(body)
        except ValueError as exc:
            return _error(str(exc), 400)

        use_llm = _as_bool(body.get("use_llm"), default=False)
        # Live prices are opt in: the request has to ask for them.
        use_pricing = _as_bool(body.get("use_pricing"), default=False)

        rec = recommend(profile)

        stack, pricing_mode = _apply_pricing(
            rec.stack,
            enabled=use_pricing and app.config["PRICING_ENABLED"],
            region=app.config["PRICING_REGION"],
        )
        if list(stack) != list(rec.stack):
            # The live prices changed the numbers, so the cost and the tips
            # are worked out again from the same recommender helpers.
            monthly_cost = round(
                sum(component.cost_month for component in stack), 2
            )
            rec = replace(
                rec,
                stack=list(stack),
                monthly_cost=monthly_cost,
                notes=build_notes(profile, monthly_cost, rec.applied_rules),
            )

        advice = None
        llm_error = ""
        if use_llm:
            advice, llm_error = _run_llm(profile, rec.stack, transport())

        if save_as:
            try:
                storage.save_plan(plans_folder(), save_as, rec, advice)
            except storage.PlanError as exc:
                return _error(f"The plan could not be saved: {exc}", 500)

        return jsonify(
            {
                "profile": _profile_json(profile),
                "monthly_cost": float(rec.monthly_cost),
                "stack": [_component_json(component) for component in rec.stack],
                "applied_rules": [_rule_json(rule) for rule in rec.applied_rules],
                "notes": list(rec.notes),
                "llm": _advice_json(advice),
                "llm_error": llm_error,
                "pricing": pricing_mode,
                "saved_as": save_as,
            }
        )

    # --- The saved plans ----------------------------------------------------

    @app.get("/api/plans")
    def list_plans():
        """The short list of the saved plans."""
        return jsonify({"plans": _plan_summaries(plans_folder())})

    @app.get("/api/plans/<name>")
    def get_plan(name: str):
        """One saved plan, with the advice it was saved with."""
        try:
            saved = storage.load_plan(plans_folder(), name)
        except storage.PlanError as exc:
            return _error(str(exc), 404)
        return jsonify(_saved_plan_json(saved))

    @app.delete("/api/plans/<name>")
    def delete_plan(name: str):
        """Delete one saved plan."""
        try:
            storage.delete_plan(plans_folder(), name)
        except storage.PlanError as exc:
            return _error(str(exc), 404)
        return jsonify({"deleted": name})

    @app.post("/api/plans/<name>/refresh")
    def refresh_plan(name: str):
        """Ask the LLM again about a saved plan and write the answer back."""
        try:
            saved = storage.load_plan(plans_folder(), name)
        except storage.PlanError as exc:
            return _error(str(exc), 404)

        advice, error = _run_llm(saved.profile, saved.recommendation.stack, transport())
        if advice is None:
            # This time the LLM was asked for, so a failure is an error.
            return _error(error or "The LLM pass could not run.", 502)

        try:
            storage.save_plan(plans_folder(), saved.name or name, saved.recommendation, advice)
            updated = storage.load_plan(plans_folder(), saved.name or name)
        except storage.PlanError as exc:
            return _error(f"The plan could not be saved: {exc}", 500)

        return jsonify(_saved_plan_json(updated))

    # --- Errors that are not JSON -------------------------------------------

    @app.errorhandler(404)
    def not_found(exc):
        if request.path.startswith("/api/"):
            return _error("That endpoint does not exist.", 404)
        return _error_page("Not found", "There is nothing at this address."), 404

    @app.errorhandler(405)
    def method_not_allowed(exc):
        if request.path.startswith("/api/"):
            return _error("That method is not allowed on this endpoint.", 405)
        return _error_page(
            "Not allowed", "That method is not allowed on this address."
        ), 405

    @app.errorhandler(500)
    def server_error(exc):
        if request.path.startswith("/api/"):
            return _error("Something went wrong inside the planner.", 500)
        return _error_page("Something went wrong", "Please try again."), 500

    return app


def _error_page(title: str, message: str) -> str:
    """A tiny HTML page for the errors that happen outside the API."""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head><body><h1>{title}</h1><p>{message}</p>"
        "<p><a href=\"/\">Back to the planner</a></p></body></html>"
    )


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def _port(value: str) -> int:
    """argparse type for --port."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port: {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("the port must be between 1 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the web app."""
    parser = argparse.ArgumentParser(
        description="Run the AI Tech Stack Planner in the browser."
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        help=f"the port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"the host to bind to (default: {DEFAULT_HOST}, localhost only)",
    )
    parser.add_argument(
        "--no-pricing",
        action="store_true",
        help="do not use the live Azure prices, only the rough estimates",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"the Azure region to price in (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--plans-dir",
        metavar="PATH",
        default=None,
        help=(
            "folder that holds the saved plans "
            "(default: the repository's plans/ folder)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Start the web app. Returns 0 when it stops cleanly."""
    args = build_parser().parse_args(argv)

    plans_dir = storage.resolve_plans_dir(args.plans_dir)
    app = create_app(
        plans_dir=plans_dir,
        pricing_enabled=not args.no_pricing,
        pricing_region=args.region,
    )

    # The URL sits on its own line on purpose: copying it out of a sentence
    # would drag the full stop along and give you a broken address.
    print("Open this URL in your browser:")
    print(f"  http://{args.host}:{args.port}")
    print("The app stops with Ctrl+C.")

    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
