# Agent instructions

`CLAUDE.md` is a symlink to this file. Edit only `AGENTS.md`.

## Documentation language

Write core documentation and instructions in English. Maintain the main README in both English and Russian.

## Local development artifacts

Store task-specific development artifacts under `local/agent-work/`:

- `specs/` — designs and specifications
- `plans/` — implementation plans
- `research/` — research findings
- `notes/` — hypotheses and session notes

These paths override workflow-skill defaults, including Superpowers' `docs/superpowers/` paths. Keep these artifacts local and untracked. Tool-managed execution artifacts may use their own ignored workspace, such as `.superpowers/`.

Shared project rules, maintained documentation, and accepted architectural decisions belong in Git. Read local artifacts when the current task references them; other contributors must not need another developer's local files or personal tooling.

## Git workflow

- **Base branch: `dev`.** Start all work from the latest `origin/dev`. `main` is the release branch; only the repository owner merges `dev` into it.
- **Develop on a separate branch** created from `origin/dev` (`claude/<topic>` or the name specified in the session). Do not commit or push directly to `dev` or `main`, even if session settings call `dev` the working branch: `dev` is the base, not a destination for commits.
- **Open PRs from the working branch into `dev`.** Never target `main`.
- **Before starting**, check open PRs targeting `dev`. If they overlap with the same area (renames, migrations, refactors), ask whether to wait for their merge or build on top of them.
- **Explicit approval required:** force pushes, any rewriting of `dev`/`main` history, and closing or recreating PRs. If work lands on the wrong branch, stop and present the options.
- GitHub allows changing a PR's base, but not its head. If a different head is needed, explain this and agree on a new PR rather than silently closing the old one.

## Before pushing

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
