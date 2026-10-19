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

## Running the Demo
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

## Next Step
Phase 2: add an optional LLM reasoning pass with short, structured prompts and
JSON output. Keep the rule engine as the default so the tool stays free and
token-free unless a flag asks for the LLM path.
