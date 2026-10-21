"""Rule engine for the AI Tech Stack Planner (Phase 1 - zero tokens, no LLM).

Where this module sits in the pipeline:

    user input (goal, budget, timeline, constraints)
        -> rules.base_stack(goal, tier)   -> the starting stack for that goal
        -> rules.match_rules(constraints)  -> the constraint rules that fire
        -> rules.apply_rules(stack, rules) -> the final stack + the rules used
        -> recommender.py                   -> prints the recommendation

Everything in here is deterministic: dictionaries, frozen dataclasses and
plain if/then logic. No network calls, no API keys, no tokens, no cost.

The three data tables (GOALS, CONSTRAINTS, TIERS, TIMELINE_BUCKETS) and the
component catalogue near the top of the file are the only places you need to
edit to tune the recommendations.
"""

from dataclasses import dataclass
from typing import Iterable

# ---------------------------------------------------------------------------
# Data tables (edit these to change the wording / the options)
# ---------------------------------------------------------------------------

GOALS: dict[str, str] = {
    "chat": "Chat / assistant app",
    "rag": "RAG - answer questions over your own documents",
    "document": "Document processing (PDFs, forms, scans)",
    "code": "Code assistant / pair programming",
}

CONSTRAINTS: dict[str, str] = {
    "needs_api": "Needs a public API in front of the model",
    "local_only": "Local only - nothing may leave my machine",
    "enterprise": "Enterprise - compliance and private networking",
    "scale": "Must handle high traffic",
    "existing_azure": "I already have an Azure subscription",
    "open_source": "Open-source models and tools only",
}

TIERS: dict[str, str] = {
    "micro": "Under $50/mo",
    "mid": "$50-$500/mo",
    "enterprise": "Over $500/mo",
}

TIMELINE_BUCKETS: dict[str, str] = {
    "quick": "Quick win (up to 4 weeks)",
    "standard": "Standard build (5-12 weeks)",
    "long": "Long project (over 12 weeks)",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    """One piece of the recommended stack.

    cost_month is a rough USD estimate per month. 0.0 means the component is
    free / open-source (you run it on hardware you already own).
    """

    name: str
    category: str  # "model" | "hosting" | "data" | "tooling" | "ops"
    purpose: str
    cost_month: float


@dataclass(frozen=True)
class Rule:
    """One if/then rule.

    trigger  - the constraint key that activates the rule
    add      - components appended to the stack
    remove   - component NAMES dropped from the stack (case-insensitive match)
    note     - one short sentence explaining the value of the rule
    """

    id: str
    description: str
    trigger: str
    add: tuple[Component, ...]
    remove: tuple[str, ...]
    note: str


# ---------------------------------------------------------------------------
# Component catalogue (edit these to change the recommended products)
# ---------------------------------------------------------------------------

# --- Models ---------------------------------------------------------------
OLLAMA = Component(
    "Ollama",
    "model",
    "Runs a small open-source model (e.g. Llama 3.1 8B) on your own machine",
    0.0,
)
AZURE_OPENAI = Component(
    "Azure OpenAI",
    "model",
    "Hosted small model (e.g. GPT-4o mini), billed per token",
    25.0,
)

# --- Hosting --------------------------------------------------------------
LOCAL_MACHINE = Component(
    "Local Machine",
    "hosting",
    "Runs the app and the model on your own laptop or server - no cloud bill",
    0.0,
)
AZURE_APP_SERVICE = Component(
    "Azure App Service",
    "hosting",
    "Managed web hosting for the app, ready to scale out",
    30.0,
)
AZURE_FUNCTIONS = Component(
    "Azure Functions",
    "hosting",
    "Serverless function app for the API layer, pay per execution",
    5.0,
)

# --- Data / retrieval -----------------------------------------------------
CHROMADB = Component(
    "ChromaDB",
    "data",
    "Local open-source vector store for retrieval (free, runs on your machine)",
    0.0,
)
AZURE_AI_SEARCH = Component(
    "Azure AI Search",
    "data",
    "Managed vector and keyword search over your documents",
    100.0,
)
AZURE_DOCUMENT_INTELLIGENCE = Component(
    "Azure AI Document Intelligence",
    "data",
    "Managed extraction of text, tables and forms from PDFs",
    75.0,
)
AZURE_BLOB_STORAGE = Component(
    "Azure Blob Storage",
    "data",
    "Stores the source documents and the extracted text",
    5.0,
)
DOCLING = Component(
    "Docling",
    "data",
    "Local open-source PDF/document parser (free)",
    0.0,
)
TESSERACT_OCR = Component(
    "Tesseract OCR",
    "data",
    "Local open-source OCR for scanned pages (free)",
    0.0,
)

# --- Tooling --------------------------------------------------------------
OPEN_WEBUI = Component(
    "Open WebUI",
    "tooling",
    "Local chat interface on top of your own model (free)",
    0.0,
)
LITELLM = Component(
    "LiteLLM",
    "tooling",
    "Open-source gateway that routes between local and hosted models",
    0.0,
)
HUGGING_FACE_HUB = Component(
    "Hugging Face Hub",
    "tooling",
    "Download open-source model weights, free to use locally",
    0.0,
)
CONTINUE = Component(
    "Continue",
    "tooling",
    "Free local code assistant inside your editor",
    0.0,
)
GITHUB_COPILOT = Component(
    "GitHub Copilot",
    "tooling",
    "Hosted code assistant with completions and chat",
    10.0,
)

# --- Ops ------------------------------------------------------------------
AZURE_MONITOR = Component(
    "Azure Monitor",
    "ops",
    "Logs, metrics and alerts for every service in the stack",
    15.0,
)
AZURE_PRIVATE_ENDPOINT = Component(
    "Azure Private Endpoint",
    "ops",
    "Private networking so traffic never leaves your virtual network",
    10.0,
)
AZURE_KEY_VAULT = Component(
    "Azure Key Vault",
    "ops",
    "Stores the keys and secrets used by the apps",
    5.0,
)
REDIS_CACHE = Component(
    "Azure Cache for Redis",
    "ops",
    "Managed cache and session store that absorbs repeated lookups",
    30.0,
)

# Every component that lives in a cloud. The "local_only" rule removes these
# by name, so keep the names here in sync with the components above.
CLOUD_COMPONENT_NAMES: tuple[str, ...] = (
    "Azure OpenAI",
    "Azure AI Search",
    "Azure AI Document Intelligence",
    "Azure Blob Storage",
    "Azure App Service",
    "Azure Functions",
    "Azure Cache for Redis",
    "Azure Key Vault",
    "Azure Monitor",
    "Azure Private Endpoint",
    "GitHub Copilot",
)


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------


def budget_tier(budget: float) -> str:
    """Map a monthly budget (USD) to a budget tier key.

    Under $50 -> "micro", $50-$500 -> "mid", over $500 -> "enterprise".
    """
    if budget < 50:
        return "micro"
    if budget <= 500:
        return "mid"
    return "enterprise"


def timeline_bucket(weeks: int) -> str:
    """Map a project length in weeks to a timeline bucket key.

    4 weeks or less -> "quick", 5-12 weeks -> "standard", more -> "long".
    """
    if weeks <= 4:
        return "quick"
    if weeks <= 12:
        return "standard"
    return "long"


# ---------------------------------------------------------------------------
# Base stacks (the starting point for a goal + budget tier)
# ---------------------------------------------------------------------------


def _core_for_tier(tier: str) -> tuple[Component, ...]:
    """The components every goal gets for a given budget tier."""
    if tier == "micro":
        # Too small for cloud bills: run everything locally for free.
        return (OLLAMA, LOCAL_MACHINE, OPEN_WEBUI)
    if tier == "enterprise":
        # Serious budget: a hosted model on managed infrastructure.
        return (AZURE_OPENAI, AZURE_APP_SERVICE)
    if tier == "mid":
        # Local model first, cheap managed hosting, one gateway to switch later.
        return (OLLAMA, AZURE_APP_SERVICE, LITELLM)
    # Unknown tier: fall back to the middle option instead of raising.
    return _core_for_tier("mid")


def _goal_extras(goal: str, tier: str) -> tuple[Component, ...]:
    """The components that only make sense for a specific goal."""
    if goal == "rag":
        # Retrieval always needs somewhere to store and search the vectors.
        if tier == "micro":
            return (CHROMADB,)
        return (AZURE_AI_SEARCH,)
    if goal == "document":
        if tier == "micro":
            return (DOCLING, TESSERACT_OCR)
        return (AZURE_DOCUMENT_INTELLIGENCE, AZURE_BLOB_STORAGE)
    if goal == "code":
        if tier == "micro":
            return (CONTINUE,)
        return (CONTINUE, GITHUB_COPILOT)
    # "chat" and any unknown goal use the plain core stack.
    return ()


def base_stack(goal: str, tier: str) -> list[Component]:
    """Return the starting stack for a project goal and a budget tier.

    The list is never empty. An unknown goal falls back to the "chat" stack
    and an unknown tier falls back to the "mid" stack.
    """
    return list(_core_for_tier(tier)) + list(_goal_extras(goal, tier))


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

# The order rules are applied in. It is fixed so the result is deterministic.
RULE_PRIORITY: tuple[str, ...] = (
    "needs_api",
    "local_only",
    "enterprise",
    "scale",
    "existing_azure",
    "open_source",
)


def _build_rule(trigger: str, tier: str = "", goal: str = "") -> Rule:
    """Build the rule for one constraint key.

    tier and goal are optional hints. They let a rule adapt a little:
      - the "enterprise" rule leaves out the search + app service when the
        budget tier is explicitly "micro"
      - the "local_only" rule adds local document tools for document projects
    """
    if trigger == "needs_api":
        return Rule(
            id="needs_api",
            description="Add a hosted model plus a serverless API layer.",
            trigger="needs_api",
            add=(AZURE_OPENAI, AZURE_FUNCTIONS),
            remove=(),
            note="A small hosted model and a serverless function keep the API cheap at low traffic.",
        )

    if trigger == "local_only":
        # Local document tools are only useful for document projects, but the
        # rule adds them when the goal is unknown so nothing is ever missing.
        local_tools: tuple[Component, ...] = ()
        if goal in ("", "document"):
            local_tools = (DOCLING, TESSERACT_OCR)
        return Rule(
            id="local_only",
            description="Drop the cloud components and run everything on your own machine.",
            trigger="local_only",
            add=(OLLAMA, CHROMADB) + local_tools,
            remove=CLOUD_COMPONENT_NAMES,
            note="No data leaves your machine - model, vector store and app all run locally.",
        )

    if trigger == "enterprise":
        # Compliance and ops components are always added; the bigger search
        # and hosting pieces come on every tier except an explicit "micro".
        add: tuple[Component, ...] = (AZURE_PRIVATE_ENDPOINT, AZURE_MONITOR)
        if tier != "micro":
            add = add + (AZURE_AI_SEARCH, AZURE_APP_SERVICE)
        return Rule(
            id="enterprise",
            description="Add compliance, private networking and ops components.",
            trigger="enterprise",
            add=add,
            remove=(),
            note="Private endpoints and monitoring cover compliance, search and App Service carry the load.",
        )

    if trigger == "scale":
        return Rule(
            id="scale",
            description="Add a managed cache so the stack survives high traffic.",
            trigger="scale",
            add=(REDIS_CACHE,),
            remove=(),
            note="A cache absorbs repeated lookups and the app can scale out automatically as load grows.",
        )

    if trigger == "existing_azure":
        return Rule(
            id="existing_azure",
            description="Reuse the Azure subscription and its standard services.",
            trigger="existing_azure",
            add=(AZURE_APP_SERVICE, AZURE_KEY_VAULT),
            remove=(),
            note="You already have Azure, so your existing credits and agreements cover this stack.",
        )

    if trigger == "open_source":
        return Rule(
            id="open_source",
            description="Add free, open-source model and tooling components.",
            trigger="open_source",
            add=(OLLAMA, LITELLM, HUGGING_FACE_HUB),
            remove=(),
            note="Open-source licence means no per-token fee, you only pay for your own hardware and hosting.",
        )

    # Unknown trigger: a harmless rule that changes nothing.
    return Rule(
        id=trigger,
        description=f"No rule defined for '{trigger}'.",
        trigger=trigger,
        add=(),
        remove=(),
        note="Nothing to do for this constraint.",
    )


def match_rules(
    constraints: Iterable[str],
    tier: str = "",
    goal: str = "",
) -> list[Rule]:
    """Return the rules triggered by the given constraint keys.

    The rules always come back in RULE_PRIORITY order, so the same input
    always gives the same output. Unknown constraint keys are ignored.
    """
    wanted = {str(key).strip().lower() for key in constraints}
    matched: list[Rule] = []
    for trigger in RULE_PRIORITY:
        if trigger in wanted:
            matched.append(_build_rule(trigger, tier=tier, goal=goal))
    return matched


# ---------------------------------------------------------------------------
# Applying rules
# ---------------------------------------------------------------------------


def apply_rules(
    stack: list[Component],
    rules: list[Rule],
) -> tuple[list[Component], list[Rule]]:
    """Apply the rules to the stack, in the order they are given.

    For every rule: components named in rule.remove are dropped first, then
    the components in rule.add are appended (skipping ones already there).
    The input list is never modified - a new list is returned together with
    the rules that actually changed the stack.
    """
    current: list[Component] = list(stack)  # copy, the caller's list is safe
    applied: list[Rule] = []

    for rule in rules:
        remove_names = {name.strip().lower() for name in rule.remove}
        kept = [c for c in current if c.name.strip().lower() not in remove_names]

        present = {c.name.strip().lower() for c in kept}
        added: list[Component] = []
        for component in rule.add:
            key = component.name.strip().lower()
            if key not in present:
                present.add(key)
                added.append(component)

        if len(kept) != len(current) or added:
            applied.append(rule)

        current = kept + added

    return current, applied


def total_monthly_cost(stack: Iterable[Component]) -> float:
    """Sum the rough monthly cost of a stack (helper for the output)."""
    return round(sum(component.cost_month for component in stack), 2)
