# AI Tech Stack Planner — Project Plan

## Goal
Build a simple application that helps plan an AI project by recommending a tech stack optimized for **utmost value and minimum cost**.

## User Profile
- Comfortable with Python, needs explanations for unfamiliar code
- Wants to publish on GitHub eventually
- Wants to keep it free and local for now (cloud later if it grows)

## Tech Stack (Phase 1)
- Language: Python
- Interface: Command Line Interface (CLI) — runs locally, no server, no cost
- No external AI API calls in Phase 1 (rule-based, zero tokens)
- Version control: Git + GitHub

## Folder Structure
```
opencode-project/
├── README.md
├── AGENTS.md
├── plan.md
├── requirements.txt
└── ai-tech-stack-planner/
    ├── src/
    │   ├── main.py          # Entry point — collects user input
    │   ├── rules.py          # If/then logic for stack recommendations (zero tokens)
    │   └── recommender.py    # Combines rules into a tech stack recommendation
    └── assets/
        └── .gitkeep
```

## Build Phases

### Phase 1: MVP — Rule-based recommender (no LLM)
- User enters: project goal, budget, timeline, constraints
- Rule engine applies simple if/then logic
- Outputs a recommended tech stack
- **Tokens used:** 0
- **Learn:** input handling, conditional logic, dictionaries, functions, output formatting

### Phase 2: Add LLM for reasoning (token-efficient)
- Short, structured prompts with JSON output
- Count tokens before/after each call
- **Tokens used:** minimal (one short call per plan)

### Phase 3: Persistence
- Save and load project plans
- **Learn:** file/database storage, schemas

### Phase 4: UI + real cost data
- Simple web interface
- Optional cloud pricing integration (Azure/AWS)
- **Learn:** frontend, API integration, cost estimation

## Component Interaction (Phase 1)
```
User input (goal, budget, timeline)
        ↓
[Rule Engine] — applies if/then logic
        ↓
[Stack Recommender] — picks tech stack based on rules
        ↓
[Output Formatter] — prints readable recommendation
```

## Example Rules
- Budget < $50 + simple chat → open-source small model + local hosting (Ollama)
- Budget $50–$500 + needs API → Azure OpenAI small model + serverless function
- Budget > $500 + enterprise needs → Azure OpenAI + Azure AI Search + App Service

## Token-Saving Guiding Rule
Use deterministic rules for everything simple; only call the LLM for hard reasoning. Cache results to avoid re-prompting.

## Commit Message Convention
Concise and descriptive (e.g., "Add demo folder structure and demo file").

## Running the Planner
```bash
python ai-tech-stack-planner/src/main.py
```

## Phase 1 Decisions (agreed during build)

- **Input**: interactive prompts by default, plus optional flags for scripted use
  (`--goal`, `--budget`, `--timeline`, `--constraints`). Flags win over prompts.
- **Output**: includes rough, hardcoded monthly cost estimates per component.
  Real pricing data stays a Phase 4 concern.
- **Goals supported**: `chat`, `rag`, `document`, `code`.
- **Constraints supported**: `needs_api`, `local_only`, `enterprise`, `scale`,
  `existing_azure`, `open_source`.
- **Testing**: Python stdlib `unittest` (zero installed packages) plus end-to-end
  CLI runs. Playwright MCP is not connected in this environment; it becomes
  relevant in Phase 4 when the web UI exists.
- **Extra folder** (not in the original tree): `ai-tech-stack-planner/tests/`.

## Phase 1 Status — DONE

Built and verified:

- `ai-tech-stack-planner/src/rules.py` — rule engine: goal/budget base stacks,
  six constraint rules, deterministic `match_rules` / `apply_rules`.
- `ai-tech-stack-planner/src/recommender.py` — `ProjectProfile`, `recommend()`,
  `format_recommendation()`.
- `ai-tech-stack-planner/src/main.py` — CLI entry point (flags + interactive prompts).
- `ai-tech-stack-planner/tests/` — 52 stdlib `unittest` tests, all passing.
- `README.md` rewritten, `requirements.txt` added (stdlib only), `.gitignore` added.

All three example rules above are covered by tests and confirmed by hand.

## Phase 2 Decisions (agreed during build)

- **Provider**: any OpenAI-compatible endpoint, called with `urllib.request` and
  `json` from the standard library. No SDK, no third-party package.
- **Scope**: the LLM only annotates the rule based stack. It cannot change a
  component, a cost or the recommendation — the rules always decide.
- **Token counting**: a local estimate before the call (characters / 4), then the
  API's reported usage after it. Both numbers are shown on one line.
- **Failure**: a missing key or a failed call falls back to the rules with a one
  line warning on stderr. Exit code stays 0, the report is unchanged.
- **Dependencies**: none. Standard library only, for the code and the tests.
- **Caching**: not yet. Caching lands with persistence in Phase 3.

## Phase 2 Status — DONE

Built and verified:

- `ai-tech-stack-planner/src/llm.py` — the only networked module: config from the
  environment, the short structured prompt, the HTTP call, JSON parsing, token
  counting and the printed reasoning block.
- `ai-tech-stack-planner/src/main.py` — gained the optional pass and the flags
  `--llm`, `--llm-model`, `--llm-base-url` and `--llm-api-key`.
- `ai-tech-stack-planner/tests/test_llm.py` — 45 stdlib `unittest` tests.
- `ai-tech-stack-planner/tests/test_cli.py` — 4 new end-to-end tests (8 in total).
- `README.md` — documents the optional pass. `requirements.txt` — still stdlib only.

101 stdlib `unittest` tests in total, all passing. The default run was confirmed
to be unchanged and to spend 0 tokens, and a missing key and a timeout were
confirmed to warn on stderr and keep exit code 0.

## Phase 3 Decisions (agreed during build)

- **One JSON file per plan**, not one big file for everything. A plan can be read,
  copied, diffed or deleted on its own, and one broken file never takes the other
  plans down with it.
- **Flags on the existing CLI**, not subcommands. `--save`, `--load`,
  `--list-plans` and `--delete` sit next to `--goal` and `--llm`, so the tool stays
  a single command.
- **A plan holds the profile, the result and the LLM advice.** Loading replays it
  without re-running the rules and without paying for the LLM a second time.
- **The plans live in the repository's `plans/` folder.** The folder is gitignored,
  and `--plans-dir` moves it somewhere else.
- **Plan names are safe file names**: letters, digits, dash and underscore only
  (1 to 64 characters, not starting with a dash), so a name can never turn into a
  path.
- **The file format is plain JSON with a `schema_version`.** A file written by a
  newer version of the tool is refused with a clear message instead of being read
  into nonsense.
- **Dependencies**: none. Standard library only, for the code and the tests.

## Phase 3 Status — DONE

Built and verified:

- `ai-tech-stack-planner/src/storage.py` — one JSON file per plan: `save_plan()`,
  `load_plan()`, `list_plans()`, `delete_plan()`, the plan name check and the
  `schema_version` handling. It prints nothing; the CLI prints.
- `ai-tech-stack-planner/src/main.py` — gained the flags `--save`, `--load`,
  `--list-plans`, `--delete`, `--plans-dir` and `--llm-refresh`.
- `ai-tech-stack-planner/tests/test_storage.py` — 40 stdlib `unittest` tests.
- `ai-tech-stack-planner/tests/test_cli.py` — 10 new end-to-end tests (18 in total).
- `README.md` — documents the saved plans. `requirements.txt` — still stdlib only.
- `.gitignore` — ignores the `plans/` folder.

151 stdlib `unittest` tests in total, all passing. The save and load round trip, the
plan list, the deletion, a bad plan name and a missing plan were confirmed by hand,
and loading a saved plan was confirmed to reuse the saved advice instead of calling
the LLM again.

## Next Step
Phase 4: UI + real cost data. A simple web interface plus optional cloud pricing
integration (Azure/AWS) for real cost estimates. The rule engine and the saved plan
format stay as they are, so the web UI can use the same modules and read the same
plan files.
