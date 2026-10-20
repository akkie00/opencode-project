# AI Tech Stack Planner

A local, free command line tool that recommends a minimal-cost AI tech stack for your
project. It uses deterministic if/then rules, so the default run costs nothing:
**zero LLM tokens, no API keys, and it works entirely offline** on the Python 3
standard library alone.

Tell it what you are building (goal), how much you can spend per month (budget), when
you need it (timeline) and what is fixed (constraints), and it prints a recommended
stack with an estimated monthly cost and a few tips.

There is also an optional LLM reasoning pass behind the `--llm` flag. It comments on
the stack the rules picked — summary, risks, suggestions, confidence. Together with
the live price list it is the only part of the tool that talks to the network, and
it stays off until you ask for it.

A plan can be saved and loaded again later. The saved plan keeps the stack and the
LLM advice, so loading it prints the same answer without paying for the LLM twice.

The same planner also runs in your browser: a small local web page with the form, the
result and the saved plans. The web app can ask Azure for the real prices of the
components it recommends, so the monthly cost is a real number instead of a guess.
Both the live prices and the LLM are off until you tick the box. The web app is the
only part of the project that needs Flask — the command line tool keeps working on
the Python standard library alone.

## How it works

```text
                    the browser                      the command line
                          │                                 │
                          ↓                                 ↓
                [Web App] (src/web.py)              [CLI] (src/main.py)
                          │                                 │
                          └──────────────┬──────────────────┘
                                         ↓
                             [Rule Engine]      — deterministic if/then logic, no LLM
                                         ↓
                             [Stack Recommender] — picks the components, adds up the cost
                                         ↓
                             [Output Formatter]  — the readable recommendation
                                         ↓
                             [LLM Pass]          — optional: comments on the stack, never changes it
                                         ↓
                             [Live Prices]       — optional: the real Azure prices
                                         ↓
                             [Storage]            — optional: the plan as one JSON file
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
- **Live prices** (`src/pricing.py`) are optional and off by default. They ask Azure's
  public price list for the real meter prices and replace the rough estimates.
- **Web app** (`src/web.py`) is the browser front end: one page, a small JSON API and
  the same rules, the same LLM pass and the same plan files.

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
    │   ├── storage.py       # Saves and loads plans as one JSON file per plan
    │   ├── pricing.py       # Live Azure prices from the public retail price list
    │   └── web.py           # The local web app — Flask, the page and the JSON API
    ├── templates/
    │   └── index.html       # The one page of the web app
    ├── static/
    │   ├── app.js           # The page logic — plain JavaScript
    │   └── style.css        # The page styles — plain CSS
    ├── tests/
    │   ├── test_rules.py        # Budget tiers, timeline buckets, rule engine
    │   ├── test_recommender.py  # Stacks, costs and the printed report
    │   ├── test_cli.py          # End-to-end runs of the command line
    │   ├── test_llm.py          # Prompt building, JSON parsing, token counting
    │   ├── test_storage.py      # Saving, loading, listing and deleting plans
    │   ├── test_pricing.py      # The live prices, the meters and the cache
    │   └── test_web.py          # The web page and the JSON API
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

## The web app

The same planner also runs in the browser: the same rules, the same optional LLM
pass and the same saved plans, on one local page. The web app is the **only** part
of the project that needs Flask — the command line tool keeps working on the
standard library alone.

### Setting it up

The web app needs one package, so it runs inside a virtual environment:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt    # Windows
source .venv/bin/activate && pip install -r requirements.txt     # macOS/Linux
```

### Starting it

```bash
.venv\Scripts\python.exe ai-tech-stack-planner/src/web.py
```

```text
Open http://127.0.0.1:8000 in your browser.
The app stops with Ctrl+C.
```

| Flag | Meaning | Default |
| --- | --- | --- |
| `--port` | the port to listen on | `8000` |
| `--host` | the host to bind to | `127.0.0.1` — localhost only, so nothing on the network can reach it |
| `--no-pricing` | do not use the live Azure prices, only the rough estimates | the prices are on |
| `--region` | the Azure region to price in | `eastus` |
| `--plans-dir` | the folder that holds the saved plans | the repository's `plans/` folder |

### The three parts of the page

1. **Your project** — the form: goal, monthly budget, timeline in weeks, the
   constraint checkboxes, an "Ask the LLM for a second opinion" checkbox, a "Use
   live Azure prices" checkbox and an optional "Save as" name.
2. **The recommendation** — the stack as a table, the monthly cost against the
   budget, a badge that says whether the prices are live or rough, the rules that
   were applied, the tips, and the LLM reasoning with the token line.
3. **Saved plans** — the list of the saved plans, with open and delete.

### Nothing leaves your machine unless you ask

Both checkboxes in the form start **unticked**. "Use live Azure prices" is off, so
a plain visit to the page never makes a network call, and "Ask the LLM for a second
opinion" is off, so no tokens are spent. You tick what you want.

### The rough prices and the live prices

Azure publishes **unit** prices, not monthly totals, so every component needs a
usage assumption. The estimate is the unit price times the assumption, and the
assumption is part of the note the module produces.

The live prices below were fetched from the real price list for `eastus` and
checked by hand:

| Component | Meter | Assumption | Live $/mo |
| --- | --- | --- | --- |
| Azure OpenAI | GPT 5 nano input, per 1M tokens | 1M input tokens | $0.05 |
| Azure App Service | P1 v3, per hour | 730 hours | $113.15 |
| Azure Functions | Standard executions, per 10 | 1M executions (the plan includes 1M free) | $0.00 |
| Azure AI Search | Standard S1, per hour | 730 hours | $245.28 |
| Azure AI Document Intelligence | S0 pre-built, per 1K pages | 1,000 pages | $10.00 |
| Azure Blob Storage | Hot LRS, per GB/month | 100 GB | $1.91 |
| Azure Cache for Redis | C0, per hour | 730 hours | $16.06 |
| Azure Key Vault | Operations, per 10K | 10,000 operations | $0.03 |
| Azure Monitor | Basic log ingestion, per GB | 10 GB of logs | $5.00 |
| Azure Private Endpoint | **no meter in the price list** | - | keeps the rough estimate |

The rough estimates in `rules.py` are **not** the same numbers, and the live ones
are usually higher, because they assume a real production size. The same RAG
project is a good example:

```bash
python ai-tech-stack-planner/src/main.py --goal rag --budget 200 --timeline 4 --constraints needs_api
```

```text
Rough estimates:  $160/mo     (App Service $30 + AI Search $100 + OpenAI $25 + Functions $5)
Live Azure prices: $358.48/mo  (App Service $113.15 + AI Search $245.28 + OpenAI $0.05)
```

That is the point of the feature: the rough number is a starting guess, the live
number is what the project would really cost at the stated usage.

**Azure Private Endpoint has no meters in the price list at all**, so it stays on
the rough estimate. A wrong price would be worse than no price.

### The 24 hour cache

The prices are cached in `.cache/azure-prices.json` in the repository root for 24
hours, so the API is asked at most once a day. The folder is in `.gitignore`.

### The JSON API

The page is a normal HTTP client, so the same things can be done with `curl`:

| Route | What it does |
| --- | --- |
| `GET /` | the page |
| `GET /api/options` | the goals, the constraints and the defaults for the form |
| `POST /api/plan` | plan a project (and optionally save it) |
| `GET /api/plans` | the saved plans |
| `GET /api/plans/<name>` | one saved plan |
| `DELETE /api/plans/<name>` | delete a saved plan |
| `POST /api/plans/<name>/refresh` | run the LLM again on a saved plan and write the advice back |

Every error is `{"error": "a message"}` with `400` (bad input), `404` (no such
plan) or `502` (the LLM call failed).

## Running the tests

The tests use the standard library `unittest` module only, so the command line
planner needs nothing installed. There are 209 tests covering the rules, the report,
the command line, the optional LLM pass, the saved plans, the live prices and the
web app. From the repository root:

```bash
python -m unittest discover -s ai-tech-stack-planner/tests -v
```

The 19 web tests **skip themselves** when Flask is not installed, so the command line
planner stays testable with no dependencies at all:

```text
Ran 209 tests in 14.4s
OK (skipped=19)
```

Inside the virtual environment all 209 run:

```bash
.venv\Scripts\python.exe -m unittest discover -s ai-tech-stack-planner/tests -v
```

```text
Ran 209 tests in 15.4s
OK
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
- **Phase 4 — UI and real cost data.** A small local web interface plus the live
  Azure prices, off by default and cached for a day. **Done.**

## License

MIT. The owner plans to publish this project on GitHub.
