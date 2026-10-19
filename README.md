# AI Tech Stack Planner

A local, free command line tool that recommends a minimal-cost AI tech stack for your
project. It uses deterministic if/then rules, so it costs nothing to run: **zero LLM
tokens, no API keys, and it works entirely offline** on the Python 3 standard library
alone.

Tell it what you are building (goal), how much you can spend per month (budget), when
you need it (timeline) and what is fixed (constraints), and it prints a recommended
stack with an estimated monthly cost and a few tips.

## How it works

```text
User input (goal, budget, timeline, constraints)
        ↓
[Rule Engine]      — deterministic if/then logic, no LLM
        ↓
[Stack Recommender] — picks the components and adds up the cost
        ↓
[Output Formatter]  — prints the readable recommendation
```

- **Rule engine** (`src/rules.py`) turns the budget into a tier (micro / mid /
  enterprise), the timeline into a bucket (quick / standard / long), builds a base
  stack for the goal and then applies the rules that match the chosen constraints.
- **Stack recommender** (`src/recommender.py`) adds up the monthly cost, writes the
  tips and formats the report.
- **CLI** (`src/main.py`) collects the profile — from flags, from a prompt, or both —
  and prints the report. It exits with `0` on success and `2` on invalid input.

## Project layout

```text
opencode-project/
├── README.md
├── AGENTS.md
├── plan.md
├── requirements.txt
└── ai-tech-stack-planner/
    ├── src/
    │   ├── main.py          # Entry point — collects user input
    │   ├── rules.py         # If/then logic for stack recommendations (zero tokens)
    │   └── recommender.py   # Combines rules into a tech stack recommendation
    ├── tests/
    │   ├── test_rules.py        # Budget tiers, timeline buckets, rule engine
    │   ├── test_recommender.py  # Stacks, costs and the printed report
    │   └── test_cli.py          # End-to-end runs of the command line
    └── assets/
        └── .gitkeep
```

## Getting started

Run it without any flags to be asked for your project step by step:

```bash
python ai-tech-stack-planner/src/main.py
```

Or pass everything on the command line:

```bash
python ai-tech-stack-planner/src/main.py --goal rag --budget 200 --timeline 4 --constraints needs_api
```

That run recommends a $160/mo stack, which fits under the $200/mo budget:

```text
Estimated monthly cost: $160/mo vs $200/mo budget — under budget
```

Available flags:

| Flag | Meaning | Example |
| --- | --- | --- |
| `--goal` | `chat`, `rag`, `document` or `code` | `--goal chat` |
| `--budget` | monthly budget in USD | `--budget 200` |
| `--timeline` | weeks until the project is needed | `--timeline 4` |
| `--constraints` | comma separated constraints | `--constraints needs_api,local_only` |

Constraints are `needs_api`, `local_only`, `enterprise`, `scale`, `existing_azure`
and `open_source`.

### When the stack costs more than your budget

The budget is a real constraint, not a wish. The tighter it is, the smaller the tier
the planner picks — and if the stack still does not fit, it says so instead of
hiding it. With a $50/mo budget the same RAG project needs $160/mo:

```bash
python ai-tech-stack-planner/src/main.py --goal rag --budget 50 --timeline 4 --constraints needs_api
```

```text
Estimated monthly cost: $160/mo vs $50/mo budget — over budget — trim or phase
```

Use that line as your prompt to drop a constraint, pick a cheaper tier, or phase
the work in stages.

## Running the tests

The tests use the standard library `unittest` module only, so nothing has to be
installed. From the repository root:

```bash
python -m unittest discover -s ai-tech-stack-planner/tests -v
```

## Project phases

- **Phase 1 — rule based recommender (no LLM).** Ask for goal, budget, timeline and
  constraints, apply if/then rules, print a stack. Tokens used: **0**.
- **Phase 2 — LLM reasoning, token efficient.** Short structured prompts with JSON
  output, counting the tokens before and after each call. Tokens used: minimal.
- **Phase 3 — persistence.** Save and load project plans (file or database schemas).
- **Phase 4 — UI and real cost data.** A simple web interface plus optional cloud
  pricing integration (Azure/AWS) for real cost estimates.

## License

MIT. The owner plans to publish this project on GitHub.
