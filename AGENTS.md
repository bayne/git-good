# AGENTS.md

## Project overview

**git-good** is an AI-powered commit message generator that works via a git `prepare-commit-msg` hook. When users include the `@@claude@@` placeholder in their commit message, the hook calls the `claude` CLI (Claude Code) with the staged diff and replaces the placeholder with a generated commit message. Since the hook runs before the editor opens, the user can review and edit the generated message before saving.

## Architecture

```
git_good/
├── __init__.py
└── main.py          # All logic lives here: CLI, hook install, message generation
tests/
├── __init__.py
├── test_unit.py     # Unit tests (mocked subprocess + anthropic)
└── test_functional.py  # Functional tests (real git repos, mocked API)
```

Single-module design — everything is in `git_good/main.py`. The entry point is the `main()` function which uses argparse with two subcommands:

- **`git-good install`** — writes a `prepare-commit-msg` hook into `.git/hooks/`
- **`git-good hook <file>`** — called by git during commit; replaces `@@claude@@` with AI-generated text

## Key constants

- `PLACEHOLDER = "@@claude@@"` — the marker users put in commit messages
- `HOOK_SCRIPT` — the shell script installed as the git hook
- `SYSTEM_PROMPT` — instructions sent to Claude for generating commit messages

## Development

- Python >= 3.14, managed with `uv`
- No Python dependencies; requires `claude` CLI (Claude Code) on PATH
- Tests: `uv run pytest tests/ -v`
- Entry point: `git-good = "git_good.main:main"` (defined in pyproject.toml)

## Testing conventions

- Unit tests mock `subprocess.run` to intercept both `git` and `claude` CLI calls
- Functional tests create real temporary git repos via `tmp_path` fixture but mock the `claude` CLI call
- All tests use pytest with `monkeypatch` for patching

## Code style

- Keep everything in `main.py` unless the module grows significantly
- Use `sys.stderr` for warnings/errors, `sys.stdout` for success messages
- Fail gracefully: if the `claude` CLI call fails, leave the placeholder as-is and print a warning
