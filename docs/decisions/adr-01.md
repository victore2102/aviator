# ADR-01: Project Name and Repository Structure

**Date:** 2026-08-07
**Status:** Complete

---

## Name — AvIator

The name of thsi tool is derived from the definition of an aviator: one who navigates and pilots a course. This maps directly to what the tool does. It navigates the user through model selection, routing each query along the most appropriate path.

The stylization **AvIator** is intentional. Capitalizing the `A` and the `I` surfaces the `AI` within the word, signaling the tool's domain without making it the entire name. The result reads naturally as a word while carrying the AI reference for anyone who looks closely.

---

## Repository Structure

```
aviator/
├── assets/
├── cli/
│   └── main.py         ← CLI entrypoint
├── docs/
│   ├── decisons/       ← ADRs and design notes live here
├── examples/
│   ├── anthropic_harness.py
├── router/
│   ├── scorer.py       ← heuristic scoring engine
├── tests/
│   ├── test_scorer.py
├── .gitignore
├── LICENSE
├── pyproject.toml
└── README.md
```

### Reasoning

Each file in the `router/` directory will have a single, named responsibility so the architecture is readable at a glance without navigating nested directories.

`cli/` is separated from `router/` so the core library can be imported and used independently of the command-line interface. A user wrapping AvIator programmatically should never need to touch anything in `cli/`.

`examples/` exists at the root level rather than inside `docs/` because examples are code, not documentation. They should be runnable and visible without digging.

`docs/` is reserved for decision records and design notes — written context that explains *why* the code is the way it is, not *what* it does.