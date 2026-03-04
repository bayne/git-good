#!/usr/bin/env python3
"""Generate an asciicast v2 .cast file by running real git-good commands.

Produces demo.cast, then calls svg-term to create demo.svg.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CAST_FILE = os.path.join(PROJECT_DIR, "demo.cast")
SVG_FILE = os.path.join(PROJECT_DIR, "demo.svg")

WIDTH = 80
HEIGHT = 24

# Mock claude CLI that returns a plausible commit message.
# Placed on PATH ahead of the real claude during demo recording.
MOCK_CLAUDE_SCRIPT = """\
#!/usr/bin/env python3
import sys, time
time.sleep(0.3)
prompt = sys.stdin.read()
if "add" in prompt.lower() or "subtract" in prompt.lower():
    print("Add arithmetic helper functions")
    print()
    print("- Introduce add() and subtract() utilities with docstrings")
    print("- Provide basic math operations for reuse across the project")
else:
    print("Update project files")
"""


class CastWriter:
    """Writes asciicast v2 format."""

    def __init__(self, path, width=80, height=24):
        self.path = path
        self.f = open(path, "w")
        self.start = time.time()
        header = {
            "version": 2,
            "width": width,
            "height": height,
            "timestamp": int(time.time()),
            "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
        }
        self.f.write(json.dumps(header) + "\n")

    def write(self, text):
        ts = time.time() - self.start
        self.f.write(json.dumps([round(ts, 6), "o", text]) + "\n")

    def type_cmd(self, cmd, delay=0.04):
        """Simulate typing a command with a prompt."""
        self.write("\x1b[1;32m$\x1b[0m ")
        for ch in cmd:
            self.write(ch)
            self.pause(delay)
        self.write("\r\n")
        self.pause(0.3)

    def output(self, text):
        self.write(text + "\r\n")

    def comment(self, text):
        self.write(f"\x1b[1;36m{text}\x1b[0m\r\n")

    def blank(self):
        self.write("\r\n")

    def pause(self, seconds):
        """Advance the timestamp."""
        time.sleep(seconds)

    def close(self):
        self.f.close()


def run(cmd, cwd=None, env=None):
    """Run a command and return stdout + stderr."""
    merged_env = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=merged_env)
    return r.stdout + r.stderr


def main():
    # Create mock claude CLI
    mock_dir = tempfile.mkdtemp()
    mock_claude = os.path.join(mock_dir, "claude")
    with open(mock_claude, "w") as f:
        f.write(MOCK_CLAUDE_SCRIPT)
    os.chmod(mock_claude, os.stat(mock_claude).st_mode | stat.S_IEXEC)

    demo_env = {
        **os.environ,
        "PATH": f"{mock_dir}:{os.environ['PATH']}",
    }

    # Create temp demo repo
    demo_dir = tempfile.mkdtemp()
    repo_dir = os.path.join(demo_dir, "my-project")

    try:
        # Set up repo
        run(["git", "init", repo_dir])
        run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir)
        run(["git", "config", "user.name", "Developer"], cwd=repo_dir)

        app_py = os.path.join(repo_dir, "app.py")
        with open(app_py, "w") as f:
            f.write(
                'def greet(name):\n'
                '    return f"Hello, {name}!"\n'
                '\n'
                'if __name__ == "__main__":\n'
                '    print(greet("world"))\n'
            )
        run(["git", "add", "app.py"], cwd=repo_dir)
        run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir)

        # Install the package into the project venv
        run(["uv", "pip", "install", "-e", "."], cwd=PROJECT_DIR)

        # ── Start recording ─────────────────────────────────────────────
        cast = CastWriter(CAST_FILE, WIDTH, HEIGHT)

        cast.blank()
        cast.comment("# git-good: AI-powered commit messages via git hooks")
        cast.pause(1.5)

        # Step 1 — install the hook
        cast.blank()
        cast.comment("# Step 1: Install the hook in your repo")
        cast.pause(0.8)

        cast.type_cmd("git-good install")
        output = run(
            ["uv", "run", "--project", PROJECT_DIR, "git-good", "install"],
            cwd=repo_dir,
            env=demo_env,
        )
        for line in output.strip().splitlines():
            cast.output(line)
        cast.pause(1.0)

        # Patch the hook so it uses `uv run` and finds the mock claude
        hook_path = os.path.join(repo_dir, ".git", "hooks", "prepare-commit-msg")
        with open(hook_path, "w") as f:
            f.write(
                f'#!/bin/sh\n'
                f'export PATH="{mock_dir}:$PATH"\n'
                f"exec uv run --project '{PROJECT_DIR}' git-good hook \"$@\"\n"
            )
        os.chmod(hook_path, 0o755)

        # Step 2 — make code changes
        cast.blank()
        cast.comment("# Step 2: Make some changes to the code")
        cast.pause(0.8)

        new_code = (
            '\n\ndef add(a, b):\n'
            '    """Add two numbers and return the result."""\n'
            '    return a + b\n'
            '\n\ndef subtract(a, b):\n'
            '    """Subtract b from a and return the result."""\n'
            '    return a - b\n'
        )
        with open(app_py, "a") as f:
            f.write(new_code)

        cast.type_cmd("git diff app.py")
        diff_output = run(["git", "diff", "app.py"], cwd=repo_dir)
        for line in diff_output.strip().splitlines():
            cast.output(line)
        cast.pause(1.0)

        # Step 3 — stage and commit with placeholder
        cast.blank()
        cast.comment("# Step 3: Stage and commit — let git-good write the message")
        cast.pause(0.8)

        cast.type_cmd("git add app.py")
        run(["git", "add", "app.py"], cwd=repo_dir)
        cast.pause(0.5)

        cast.type_cmd("git commit -m '@@ai@@'")
        commit_output = run(
            ["git", "commit", "-m", "@@ai@@"],
            cwd=repo_dir,
            env=demo_env,
        )
        for line in commit_output.strip().splitlines():
            if line.strip():
                cast.output(line)
        cast.pause(1.0)

        # Step 4 — show the result
        cast.blank()
        cast.comment("# The placeholder was replaced with an AI-generated message!")
        cast.pause(0.8)

        cast.type_cmd("git log --oneline -2")
        log_output = run(["git", "log", "--oneline", "-2"], cwd=repo_dir)
        for line in log_output.strip().splitlines():
            cast.output(line)
        cast.pause(2.0)

        cast.blank()
        cast.comment("# git-good turns your diffs into meaningful commit messages.")
        cast.pause(2.0)

        cast.close()
        print(f"Cast file written to: {CAST_FILE}")

        # Convert to SVG
        subprocess.run(
            [
                "svg-term",
                "--in", CAST_FILE,
                "--out", SVG_FILE,
                "--window",
                "--no-cursor",
                "--width", str(WIDTH),
                "--height", str(HEIGHT),
                "--from", "500",
            ],
            check=True,
        )
        print(f"SVG written to: {SVG_FILE}")

    finally:
        shutil.rmtree(demo_dir, ignore_errors=True)
        shutil.rmtree(mock_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
