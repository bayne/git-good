# AGENTS.md

## Project overview

**git-good** is an AI-powered commit message generator that works via a git `prepare-commit-msg` hook. When users include the `@@ai@@` placeholder in their commit message, the hook calls the Anthropic API directly (Claude Haiku) with the staged diff and full file contents for context, and replaces the placeholder with a generated commit message. Since the hook runs before the editor opens, the user can review and edit the generated message before saving.

## Architecture

```
git_good/
├── __init__.py
└── main.py          # All logic lives here: CLI, hook install, message generation
tests/
├── __init__.py
├── test_unit.py     # Unit tests (mocked subprocess + anthropic)
└── test_functional.py  # Functional tests (real git repos, mocked API)
scripts/
└── record_demo.py   # Generates demo.cast and demo.svg
```

Single-module design — everything is in `git_good/main.py`. The entry point is the `main()` function which uses argparse with two subcommands:

- **`git-good install`** — writes a `prepare-commit-msg` hook into `.git/hooks/`
- **`git-good hook <file>`** — called by git during commit; replaces `@@ai@@` with AI-generated text

## Key constants

- `PLACEHOLDER = "@@ai@@"` — the marker users put in commit messages
- `HOOK_SCRIPT` — the shell script installed as the git hook
- `SYSTEM_PROMPT` — instructions sent to Claude for generating commit messages

## Development

- Python >= 3.14, managed with `uv`
- Dependency: `anthropic` SDK; requires `ANTHROPIC_API_KEY` env var
- Tests: `uv run pytest tests/ -v`
- Demo: `uv run python scripts/record_demo.py`
- Entry point: `git-good = "git_good.main:main"` (defined in pyproject.toml)

## Testing conventions

- Unit tests mock `subprocess.run` for git commands and `_run_api_with_spinner` for API calls
- Functional tests create real temporary git repos via `tmp_path` fixture but mock `_run_api_with_spinner`
- All tests use pytest with `monkeypatch` for patching

## Code style

- Keep everything in `main.py` unless the module grows significantly
- Use `sys.stderr` for warnings/errors, `sys.stdout` for success messages
- Fail gracefully: if the API call fails, leave the placeholder as-is and print a warning
