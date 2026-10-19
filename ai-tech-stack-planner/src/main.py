"""Command line entry point for the AI Tech Stack Planner.

The program asks for a small project profile -- goal, budget, timeline and
constraints -- and then prints the tech stack recommended by the rule based
recommender.  Every value can be given on the command line; anything that is
missing is asked for at the prompt.

Examples
--------
    python ai-tech-stack-planner/src/main.py
    python ai-tech-stack-planner/src/main.py --goal chat --budget 30 --timeline 4
    python ai-tech-stack-planner/src/main.py --goal rag --constraints needs_api,enterprise
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import TypeVar

from recommender import ProjectProfile, format_recommendation, recommend
from rules import CONSTRAINTS, GOALS

# --- Defaults ---------------------------------------------------------------

#: Goal used when the user just presses Enter.
DEFAULT_GOAL = "chat"
#: Monthly budget in USD used when the user just presses Enter.
DEFAULT_BUDGET = 100.0
#: Weeks until the project is needed, used when the user just presses Enter.
DEFAULT_TIMELINE = 4

#: How many times we ask again before falling back to the default value.
MAX_ATTEMPTS = 3

#: Helper type so the helpers below can work with any kind of answer.
T = TypeVar("T")


class UsageError(Exception):
    """Raised when a valid profile cannot be collected (bad or missing input)."""


# --- Small input helpers ----------------------------------------------------


def _stdin_is_interactive() -> bool:
    """Return True when we can safely ask the user for missing values."""
    if sys.stdin is None:
        return False
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):  # closed or replaced stdin
        return False


def _read_line(prompt: str) -> str | None:
    """Show `prompt` and read one line of input.

    Returns None when there is no more input (Ctrl-D, Ctrl-Z or a closed pipe).
    The prompt is printed here (instead of being handed to input()) so that it
    always shows up before any error message.
    """
    print(prompt, end="", flush=True)
    try:
        return input()
    except EOFError:
        return None


def _ask_until(
    prompt: str,
    parse: Callable[[str], T | None],
    default: T,
    flag: str,
    hint: str = "",
) -> T:
    """Ask a question until the answer is valid.

    `parse` turns the typed text into a value, or returns None when the
    answer is not valid.  An empty answer accepts `default`, and so does a
    run of MAX_ATTEMPTS invalid answers.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        answer = _read_line(prompt)
        if answer is None:
            # No more input: a closed pipe, or Ctrl-D / Ctrl-Z in a terminal.
            reason = (
                "input ended (Ctrl-D / Ctrl-Z)"
                if _stdin_is_interactive()
                else "standard input is not interactive"
            )
            raise UsageError(
                f"Missing value for {flag}: {reason}, so the planner cannot ask "
                f"for it. Pass {flag} on the command line."
            )

        answer = answer.strip()
        if not answer:
            return default  # Enter accepts the default.

        value = parse(answer)
        if value is not None:
            return value

        if attempt < MAX_ATTEMPTS:
            print(f"  Invalid answer {answer!r} - {hint}. Please try again.")

    print(f"  Using the default for {flag} after {MAX_ATTEMPTS} invalid answers.")
    return default


# --- Answer parsers ---------------------------------------------------------


def _parse_goal(text: str) -> str | None:
    """Turn "1", "chat" or "CHAT" into the goal key "chat"."""
    cleaned = text.strip().lower()
    if cleaned in GOALS:
        return cleaned
    if cleaned.isdigit():
        index = int(cleaned)
        keys = list(GOALS)
        if 1 <= index <= len(keys):
            return keys[index - 1]
    return None


def _parse_budget(text: str) -> float | None:
    """Turn "30" or "30.5" into a monthly budget, or None when invalid."""
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def _parse_timeline(text: str) -> int | None:
    """Turn "4" into a number of weeks, or None when invalid."""
    try:
        value = int(text)
    except ValueError:
        return None
    if value < 1:
        return None
    return value


def _match_constraint(text: str) -> str | None:
    """Turn "1" or "local_only" into the constraint key "local_only"."""
    cleaned = text.strip().lower()
    if cleaned in CONSTRAINTS:
        return cleaned
    if cleaned.isdigit():
        index = int(cleaned)
        keys = list(CONSTRAINTS)
        if 1 <= index <= len(keys):
            return keys[index - 1]
    return None


def _parse_constraints(text: str) -> list[str] | None:
    """Turn "1, local_only" into ["needs_api", "local_only"].

    Returns None as soon as one of the entries is not a known constraint.
    """
    parts = [part.strip() for part in text.split(",")]
    parts = [part for part in parts if part]
    if not parts:
        return []

    chosen: list[str] = []
    for part in parts:
        key = _match_constraint(part)
        if key is None:
            return None
        if key not in chosen:  # keep the order, drop duplicates
            chosen.append(key)
    return chosen


def _parse_constraint_flags(text: str) -> list[str]:
    """Validate the --constraints command line value.

    Unknown names are an error: we never silently drop a constraint.
    """
    chosen: list[str] = []
    for part in text.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in CONSTRAINTS:
            raise UsageError(
                f"Unknown constraint {name!r}. "
                f"Valid constraints: {', '.join(CONSTRAINTS)}"
            )
        if name not in chosen:
            chosen.append(name)
    return chosen


# --- Command line -----------------------------------------------------------


def _budget_arg(text: str) -> float:
    """argparse type for --budget: a number that is 0 or bigger."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("budget must be 0 or more")
    return value


def _timeline_arg(text: str) -> int:
    """argparse type for --timeline: a whole number of weeks, 1 or more."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("timeline must be 1 week or more")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the command line."""
    parser = argparse.ArgumentParser(
        description=(
            "Recommend a minimal cost AI tech stack for your project. "
            "Anything you leave out is asked for interactively."
        )
    )
    parser.add_argument(
        "--goal",
        choices=list(GOALS),
        help="project goal (default: asked for)",
    )
    parser.add_argument(
        "--budget",
        type=_budget_arg,
        help="monthly budget in USD, 0 or more (default: asked for)",
    )
    parser.add_argument(
        "--timeline",
        type=_timeline_arg,
        help="weeks until the project is needed, 1 or more (default: asked for)",
    )
    parser.add_argument(
        "--constraints",
        default=None,
        help=(
            "comma separated constraints, e.g. 'needs_api,local_only' "
            "(default: asked for)"
        ),
    )
    return parser


# --- Prompts ----------------------------------------------------------------


def _goal_prompt() -> str:
    """Build the goal question, including the numbered list of goals."""
    lines = ["Choose a project goal:"]
    for number, (key, label) in enumerate(GOALS.items(), start=1):
        lines.append(f"  {number}) {key} - {label}")
    lines.append(f"Enter a number (1-{len(GOALS)}) or a key [{DEFAULT_GOAL}]: ")
    return "\n".join(lines)


def _constraints_prompt() -> str:
    """Build the constraints question, including the numbered list."""
    lines = ["Choose any constraints (comma separated):"]
    for number, (key, label) in enumerate(CONSTRAINTS.items(), start=1):
        lines.append(f"  {number}) {key} - {label}")
    lines.append("Enter numbers or keys, blank for none: ")
    return "\n".join(lines)


# --- Entry points -----------------------------------------------------------


def collect_profile(argv: list[str] | None = None) -> ProjectProfile:
    """Collect the project profile from flags and/or interactive questions."""
    args = build_parser().parse_args(argv)

    # Fail fast on a bad --constraints value, before we ask any questions.
    constraints: list[str] | None = None
    if args.constraints is not None:
        constraints = _parse_constraint_flags(args.constraints)

    # Flags that were not given on the command line; used in the error message
    # when there is no input left to ask with.
    not_given = [
        flag
        for flag, value in (
            ("--goal", args.goal),
            ("--budget", args.budget),
            ("--timeline", args.timeline),
            ("--constraints", args.constraints),
        )
        if value is None
    ]

    try:
        goal = args.goal
        if goal is None:
            goal = _ask_until(
                _goal_prompt(),
                _parse_goal,
                DEFAULT_GOAL,
                "--goal",
                "expected a number from the list or a known goal key",
            )

        budget = args.budget
        if budget is None:
            budget = _ask_until(
                f"Monthly budget in USD (0 or more) [{DEFAULT_BUDGET:g}]: ",
                _parse_budget,
                DEFAULT_BUDGET,
                "--budget",
                "expected a number of 0 or more",
            )

        timeline = args.timeline
        if timeline is None:
            timeline = _ask_until(
                f"Weeks until the project is needed (1 or more) [{DEFAULT_TIMELINE}]: ",
                _parse_timeline,
                DEFAULT_TIMELINE,
                "--timeline",
                "expected a whole number of weeks, 1 or more",
            )

        if constraints is None:
            constraints = _ask_until(
                _constraints_prompt(),
                _parse_constraints,
                [],
                "--constraints",
                "expected known constraint names or numbers",
            )
    except UsageError as exc:
        # Add the list of flags that still have to be given on the command line.
        if not_given:
            extra = f" Flags to give on the command line: {', '.join(not_given)}."
        else:
            extra = ""
        raise UsageError(f"{exc}{extra}") from None

    return ProjectProfile(
        goal=goal,
        budget=budget,
        timeline_weeks=timeline,
        constraints=constraints,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the planner. Returns 0 on success and 2 on invalid input."""
    try:
        profile = collect_profile(argv)
    except UsageError as exc:
        sys.stdout.flush()  # keep the prompts in front of the error message
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        # argparse already printed the problem (for example --help or a bad flag).
        code = exc.code
        return code if isinstance(code, int) else 2

    print(format_recommendation(recommend(profile)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
