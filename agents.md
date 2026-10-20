# Agents

This file provides guidance for AI agents working on the `opencode-project` repository.


## Responses
- keep responses concise and to the point, unless the user asks for more clarity. 

## Plan mode

- Always ask for clarifying questions 
- Never assume design, tech stack, or features
- Suggest any idea or example if user doesn't understand what is being said or asks to further clariy it. 

## Change / EDIT MODE

- Never implement features yourself when possible - use sub-agents
- Identify the changes from the plan that can be implemented in parallel, and use sub-agents to implement the features efficiently. 
- When using sub-agents to implement features, act as a coordinator only. 

## TESTING
- Never assume changes will simply work, always test
- If testing tools are not available, ask the user if the testing can be skipped or provide options on how to run a quick test. 

## Project Overview

`opencode-project` hosts the **AI Tech Stack Planner**: a local, free command line tool that recommends a minimal-cost AI tech stack for a project.

- It asks for a goal, a monthly budget, a timeline and constraints, applies deterministic if/then rules, and prints a recommended stack with a rough monthly cost.
- The default run is free and offline: **0 LLM tokens, no API keys, no network access**. The optional `--llm` flag adds a reasoning pass that only comments on the stack the rules picked — the rules always decide.
- Python 3 standard library only. There is nothing to install.
- **Phase 1** (rule based recommender) and **Phase 2** (optional LLM reasoning pass with token counting) are done. **Phase 3** (persistence — save and load project plans) is next.

## Folder Structure

```text
opencode-project/
├── README.md                # User facing documentation of the planner
├── AGENTS.md                # Guidance for AI agents working in this repository
├── plan.md                  # Project plan, phase decisions and status
├── requirements.txt         # Intentionally empty — standard library only
├── .gitignore               # Ignores Python bytecode and caches
└── ai-tech-stack-planner/
    ├── src/
    │   ├── main.py          # CLI entry point — collects the profile, prints the report
    │   ├── rules.py         # the rule engine — deterministic if/then logic (zero tokens)
    │   ├── recommender.py   # combines the rules into a stack + the printed report
    │   └── llm.py           # optional LLM reasoning pass — the only networked module
    ├── tests/
    │   ├── test_rules.py        # budget tiers, timeline buckets, the rule engine
    │   ├── test_recommender.py  # stacks, costs and the printed report
    │   ├── test_cli.py          # end-to-end runs of the command line
    │   └── test_llm.py          # prompt building, JSON parsing, token counting
    └── assets/
        └── .gitkeep
```

## Conventions

- Commit messages should be concise and descriptive (e.g., "Add rule engine for budget tiers").
- Keep the project minimal — do not add production code without discussion.
- Stay on the Python standard library unless the user agrees otherwise; no third-party packages without explicit approval.
- Place new source files under `ai-tech-stack-planner/src/`, tests under `ai-tech-stack-planner/tests/`, and supporting assets under `ai-tech-stack-planner/assets/`.

## Running the Planner

```bash
python ai-tech-stack-planner/src/main.py
python ai-tech-stack-planner/src/main.py --goal rag --budget 200 --timeline 4 --constraints needs_api
python -m unittest discover -s ai-tech-stack-planner/tests -v
```
