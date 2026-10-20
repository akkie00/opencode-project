# AI Tech Stack Planner

A local, free command line tool that recommends a minimal-cost AI tech stack for your
project. It uses deterministic if/then rules, so the default run costs nothing:
**zero LLM tokens, no API keys, and it works entirely offline** on the Python 3
standard library alone.

Tell it what you are building (goal), how much you can spend per month (budget), when
you need it (timeline) and what is fixed (constraints), and it prints a recommended
stack with an estimated monthly cost and a few tips.

There is also an optional LLM reasoning pass behind the `--llm` flag. It comments on
the stack the rules picked — summary, risks, suggestions, confidence. It is the only
part of the tool that talks to the network, and it stays off until you ask for it.

A plan can be saved and loaded again later. The saved plan keeps the stack and the
LLM advice, so loading it prints the same answer without paying for the LLM twice.

## How it works

```text
User input (goal, budget, timeline, constraints)
        ↓
[Rule Engine]      — deterministic if/then logic, no LLM
        ↓
[Stack Recommender] — picks the components and adds up the cost
        ↓
[Output Formatter]  — prints the readable recommendation
        ↓
[LLM Pass]          — optional (--llm): comments on the stack, never changes it
        ↓
[Storage]            — optional (--save): writes the plan as one JSON file
```

- **Rule engine** (`src/rules.py`) turns the budget into a tier (micro / mid /
  enterprise), the timeline into a bucket (quick / standard / long), builds a base
  stack for the goal and then applies the rules that match the chosen constraints.
- **Stack recommender** (`src/recommender.py`) adds up the monthly cost, writes the
  tips and formats the report.
- **CLI** (`src/main.py`) collects the profile — from flags, from a prompt, or both —
  and prints the report. It exits with `0` on success and `2` on invalid input.
- **LLM pass** (`src/llm.py`) is optional and off by default. With `--llm` it asks an
  OpenAI-compatible model to comment on the finished stack and counts the tokens it
  used.
- **Storage** (`src/storage.py`) saves the plan — the profile, the recommendation and
  the LLM advice — as one JSON file in `plans/`, and reads it back without running
  the rules or the LLM again.

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
    │   ├── recommender.py   # Combines rules into a tech stack recommendation
    │   ├── llm.py           # Optional LLM reasoning pass — the only networked module
    │   └── storage.py       # Saves and loads plans as one JSON file per plan
    ├── tests/
    │   ├── test_rules.py        # Budget tiers, timeline buckets, rule engine
    │   ├── test_recommender.py  # Stacks, costs and the printed report
    │   ├── test_cli.py          # End-to-end runs of the command line
    │   ├── test_llm.py          # Prompt building, JSON parsing, token counting
    │   └── test_storage.py      # Saving, loading, listing and deleting plans
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

## Optional LLM reasoning

The rule based report is the answer. The LLM pass is an optional second opinion on
top of it — it explains the stack, it never changes it.

The pass is **off by default**. Without `--llm` no environment variable is read, no
network call is made and the tool stays free, offline and at **0 tokens**. Turn it
on with `--llm`:

```bash
python ai-tech-stack-planner/src/main.py --goal rag --budget 200 --timeline 4 --constraints needs_api --llm
```

| Flag | Meaning | Default |
| --- | --- | --- |
| `--llm` | add the optional LLM reasoning pass (uses tokens) | off |
| `--llm-model` | model to call, overrides `LLM_MODEL` | `gpt-4o-mini` |
| `--llm-base-url` | OpenAI-compatible endpoint, overrides `LLM_BASE_URL` | `https://api.openai.com/v1` |
| `--llm-api-key` | API key, overrides `LLM_API_KEY` | none |

The environment variables are `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and
`LLM_TIMEOUT`. They are only read when `--llm` is given, and the flags always win
over the environment. Prefer the environment variable for the key so it stays out of
your shell history:

```bash
export LLM_API_KEY=sk-...
```

### Any OpenAI-compatible provider

The tool speaks the standard OpenAI chat API shape, so any compatible server works:

| Provider | Base URL | Notes |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | the default |
| Azure OpenAI | `https://<resource>.openai.azure.com/openai/v1` | use your deployment name as the model |
| Ollama (local) | `http://127.0.0.1:11434/v1` | runs on your own machine |

Ollama needs no key, but the tool refuses to call the network without one, so set
`LLM_API_KEY=ollama` when you use it.

### The rules still decide

The rule engine picks the stack and the cost, exactly as before. The LLM only reads
the finished stack and adds a summary, the real risks, a few suggestions and a
confidence score. It cannot swap a component or change the price.

### The token line

One line is printed after the reasoning block:

```text
LLM tokens: ~223 estimated prompt -> 189 prompt + 96 completion = 285 total
```

- `~223` is the local estimate of the prompt size, counted **before** the call
  (characters / 4 — the usual rough rule of thumb).
- `189 prompt + 96 completion = 285 total` are the real numbers the API reported in
  its `usage` block **after** the call.
- When the server reports no usage at all, the line reads
  `LLM tokens: ~223 estimated prompt -> usage not reported by the API`.

### A real run

The rule based part of the report is unchanged. The two extra blocks are the
reasoning and the token line:

```text
==========================================================
AI Tech Stack Recommendation — RAG - answer questions over your own documents ($50-$500/mo)

Profile
----------------------------------------------------------
Goal:        RAG - answer questions over your own documents
Budget:      $200/mo
Timeline:    4 weeks (Quick win (up to 4 weeks))
Constraints: Needs a public API in front of the model

Recommended stack
----------------------------------------------------------
  - Ollama [model] — Runs a small open-source model (e.g. Llama 3.1 8B) on your own machine (~$0/mo)
  - Azure App Service [hosting] — Managed web hosting for the app, ready to scale out (~$30/mo)
  - LiteLLM [tooling] — Open-source gateway that routes between local and hosted models (~$0/mo)
  - Azure AI Search [data] — Managed vector and keyword search over your documents (~$100/mo)
  - Azure OpenAI [model] — Hosted small model (e.g. GPT-4o mini), billed per token (~$25/mo)
  - Azure Functions [hosting] — Serverless function app for the API layer, pay per execution (~$5/mo)

Estimated monthly cost: $160/mo vs $200/mo budget — under budget

Why this stack
----------------------------------------------------------
  - Add a hosted model plus a serverless API layer. (A small hosted model and a serverless function keep the API cheap at low traffic.)

Tips
----------------------------------------------------------
  - Estimated $160/mo vs your $200/mo budget — under budget.
  - Timeline is tight — favour ready-made services over custom training.
  - Start small and scale later: ship the smallest stack that proves the idea, then upgrade as usage grows.
  - A small hosted model and a serverless function keep the API cheap at low traffic.

==========================================================
LLM reasoning
----------------------------------------------------------
Summary:     The stack fits the budget, but the hosted model is the only monthly cost that grows with usage.
Confidence:  high
Risks:
  - No redundancy, so a single Azure region outage stops the app.
  - Retrieval cost is fixed, so heavy traffic will not blow the budget.
Suggestions:
  - Add a caching layer before traffic grows.
  - Start with the small model and upgrade only if quality needs it.
==========================================================
LLM tokens: ~223 estimated prompt -> 189 prompt + 96 completion = 285 total
```

### When the LLM call fails

The LLM pass can never break the tool. A missing key or a failed call prints a
one-line warning on stderr, the rule based report is printed exactly as usual, and
the exit code stays `0`:

```text
LLM pass skipped: no API key set (LLM_API_KEY).
LLM pass failed: the request timed out after 2.0 seconds - the rule based recommendation above is unchanged.
```

## Saving and loading plans

A plan is worth keeping: the profile you gave, the stack the rules picked and the
LLM advice (if there was one). `--save` writes it to disk, `--load` prints it back.

| Flag | Meaning |
| --- | --- |
| `--save NAME` | save the plan under this name (one JSON file per plan) |
| `--load NAME` | load a saved plan and print it |
| `--list-plans` | list the saved plans |
| `--delete NAME` | delete a saved plan |
| `--plans-dir PATH` | the folder that holds the plans (default: the repository's `plans/` folder) |
| `--llm-refresh` | with `--load`, get fresh LLM advice instead of the saved advice |

Save a plan by adding `--save` to any normal run:

```bash
python ai-tech-stack-planner/src/main.py --goal chat --budget 30 --timeline 4 --constraints local_only --save my-chat
```

```text
Saved plan 'my-chat' to /path/to/opencode-project/plans/my-chat.json.
```

The path after `to` is the file that was written, so it shows the full path on your
machine.

One plan is one JSON file inside the `plans/` folder, so a plan can be read, copied
or deleted on its own. The folder is created when it does not exist yet, and it is
in `.gitignore` — a saved plan is never committed by accident.

Use `--plans-dir` to keep the plans somewhere else:

```bash
python ai-tech-stack-planner/src/main.py --goal chat --budget 30 --timeline 4 --save my-chat --plans-dir ~/my-plans
```

### Loading replays the plan

`--load` prints the plan exactly as it was saved. It does not re-run the rules, it
asks no questions, and it does **not** call the LLM again — the advice you already
paid for is reused:

```bash
python ai-tech-stack-planner/src/main.py --load my-chat
```

```text
Note: the LLM advice came from the saved plan, so no new tokens were spent.
```

`--load --llm` still reuses the saved advice. Add `--llm-refresh` when you want a
fresh opinion instead — that is the only case that spends tokens a second time:

```bash
python ai-tech-stack-planner/src/main.py --load my-chat --llm-refresh
```

Without `--load` the flag is refused:

```text
Error: --llm-refresh only makes sense with --load.
```

### Listing and deleting

```bash
python ai-tech-stack-planner/src/main.py --list-plans
```

```text
Saved plans in plans/
  demo-plan      chat     $0/mo     saved 2026-10-20 23:06
```

An empty folder says so:

```text
No saved plans in plans/.
```

One plan is removed by its name:

```bash
python ai-tech-stack-planner/src/main.py --delete my-chat
```

```text
Deleted plan 'my-chat'.
```

### The plan name

A plan name may only use letters, digits, dash and underscore — 1 to 64 characters,
and it cannot start with a dash. A name like `bad/name` is refused, so a name can
never turn into a path.

Every problem — a bad name, a missing plan, a file that cannot be read — is
reported the same way, on stderr, with exit code 2. The message names the plans
folder the plan should have been in:

```text
Error: there is no saved plan called 'nope' in /path/to/opencode-project/plans
```

### The saved file

`plans/my-chat.json` is plain, human-readable JSON:

```json
{
  "schema_version": 1,
  "name": "demo-plan",
  "saved_at": "2026-10-20T23:06:05",
  "profile": {
    "goal": "chat",
    "budget": 30.0,
    "timeline_weeks": 4,
    "constraints": [
      "local_only"
    ]
  },
  "recommendation": {
    "monthly_cost": 0.0,
    "stack": [
      {
        "name": "Ollama",
        "category": "model",
        "purpose": "Runs a small open-source model (e.g. Llama 3.1 8B) on your own machine",
        "cost_month": 0.0
      }
    ],
    "applied_rules": [
      {
        "id": "local_only",
        "description": "Drop the cloud components and run everything on your own machine.",
        "trigger": "local_only",
        "note": "No data leaves your machine - model, vector store and app all run locally."
      }
    ],
    "notes": [
      "Estimated $0/mo vs your $30/mo budget — under budget."
    ]
  },
  "llm": null
}
```

The `llm` block is `null` when the plan was saved without an LLM pass. When there
was one it holds the summary, the risks, the suggestions, the confidence, the
prompt estimate and the three token numbers.

## Running the tests

The tests use the standard library `unittest` module only, so nothing has to be
installed. There are 151 tests covering the rules, the report, the command line, the
optional LLM pass and the saved plans. From the repository root:

```bash
python -m unittest discover -s ai-tech-stack-planner/tests -v
```

## Project phases

- **Phase 1 — rule based recommender (no LLM).** Ask for goal, budget, timeline and
  constraints, apply if/then rules, print a stack. Tokens used: **0**. **Done.**
- **Phase 2 — LLM reasoning, token efficient.** Short structured prompts with JSON
  output, counting the tokens before and after each call. Optional, behind `--llm`;
  the rules still decide the stack. Tokens used: minimal. **Done.**
- **Phase 3 — persistence.** Save and load project plans as one JSON file per plan
  in `plans/`. Loading a plan replays it without running the rules or paying for
  the LLM again. **Done.**
- **Phase 4 — UI and real cost data.** A simple web interface plus optional cloud
  pricing integration (Azure/AWS) for real cost estimates.

## License

MIT. The owner plans to publish this project on GitHub.
