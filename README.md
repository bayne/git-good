# git-good

AI-powered commit message generation via git hooks. Write `@@claude@@` as your commit message and let Claude fill it in.

## Demo

[![git-good demo](./demo.svg)](./demo.svg)

## How it works

1. Install the git hook into your repo
2. When you commit, use `@@claude@@` as your message (or part of it)
3. The hook sends your staged diff to Claude and replaces the placeholder with a generated commit message

## Installation

```bash
# Install the package
uv pip install git-good

# In your git repo, install the hook
git-good install
```

## Usage

```bash
# Stage your changes
git add -A

# Commit with the placeholder — Claude writes the message
git commit -m "@@claude@@"

# You can also use it as part of a message
git commit -m "@@claude@@

Co-authored-by: Me <me@example.com>"
```

## Requirements

- Python >= 3.14
- An `ANTHROPIC_API_KEY` environment variable set with your API key

## Development

```bash
# Clone and install dev dependencies
git clone https://github.com/youruser/git-good.git
cd git-good
uv sync

# Run tests
uv run pytest tests/ -v
```

## License

MIT
