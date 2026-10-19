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

`opencode-project` is a simple demo repository used to demonstrate a basic folder structure and Git workflow.

## Folder Structure

```text
opencode-project/
├── README.md
├── agents.md
└── demo/
    ├── src/
    │   └── demo.py
    └── assets/
        └── .gitkeep
```

## Conventions

- Commit messages should be concise and descriptive (e.g., "Add demo folder structure and demo file").
- Keep the demo minimal — do not add production code without discussion.
- Place new source files under `demo/src/` and supporting assets under `demo/assets/`.

## Running the Demo

```bash
python demo/src/demo.py
```
