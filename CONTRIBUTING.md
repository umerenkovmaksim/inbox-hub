# Contributing

[English README](README.md) · [Russian README](README.ru.md)

Development requires Python 3.14+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

## Environment and checks

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The MCP adapter is a separate package with its own lockfile:

```bash
uv sync --locked --directory mcp_adapter
uv run --directory mcp_adapter python -m pytest
```

Tests use temporary databases and mock external calls. They do not require real credentials, a running service, or a `.env` file. For a manual application run, follow the [configuration guide](docs/configuration.md).

[.github/workflows/ci.yml](.github/workflows/ci.yml) defines Ruff checks and server and MCP tests on Python 3.14. The workflow runs for PRs targeting `dev`/`main` and pushes to those branches.

## Changes and pull requests

- Start from the latest `origin/dev` on a separate working branch. Check open PRs targeting `dev` for overlapping work before you begin.
- Target `dev` with your PR. `main` is the release branch; the owner merges changes from `dev`.
- Run the checks above before pushing. Describe the problem, resulting behavior, and validation in the PR.
- Write core documentation and instructions in English. Keep the English and Russian READMEs aligned when changing features or the quick start.
- Use fictional data in examples and tests. Keep tokens, session files, personal catalogs, and collected materials out of commits.

See [CONTEXT.md](CONTEXT.md) for terminology and [AGENTS.md](AGENTS.md) for agent instructions. Historical plans in `docs/superpowers/` record earlier development; use the README links for current operational guides.
