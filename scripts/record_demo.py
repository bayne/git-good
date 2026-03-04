#!/usr/bin/env python3
"""Generate an asciicast v2 .cast file by running real git-good commands.

Produces demo.cast, then calls svg-term to create demo.svg.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CAST_FILE = os.path.join(PROJECT_DIR, "demo.cast")
SVG_FILE = os.path.join(PROJECT_DIR, "demo.svg")
MOCK_PORT = 18924

WIDTH = 80
HEIGHT = 24


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
    """Run a command and return stdout."""
    merged_env = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=merged_env)
    return r.stdout + r.stderr


def main():
    # Start mock API server
    mock_env = {**os.environ}
    mock_proc = subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "mock_api.py"), str(MOCK_PORT)],
        stdout=subprocess.PIPE,
        text=True,
        env=mock_env,
    )
    mock_proc.stdout.readline()  # wait for port print
    time.sleep(0.3)

    demo_env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "sk-ant-demo-key",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}",
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
            f.write('def greet(name):\n    return f"Hello, {name}!"\n\nif __name__ == "__main__":\n    print(greet("world"))\n')
        run(["git", "add", "app.py"], cwd=repo_dir)
        run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir)

        # Install the package
        run(["uv", "pip", "install", "-e", "."], cwd=PROJECT_DIR)

        # Start recording
        cast = CastWriter(CAST_FILE, WIDTH, HEIGHT)

        cast.blank()
        cast.comment("# git-good: AI-powered commit messages via git hooks")
        cast.pause(1.5)

        cast.blank()
        cast.comment("# Step 1: Install the hook in your repo")
        cast.pause(0.8)

        install_cmd = f"git-good install"
        cast.type_cmd(install_cmd)
        output = run(
            ["uv", "run", "--project", PROJECT_DIR, "git-good", "install"],
            cwd=repo_dir,
            env=demo_env,
        )
        for line in output.strip().splitlines():
            cast.output(line)
        cast.pause(1.0)

        # Patch the hook to use uv run
        hook_path = os.path.join(repo_dir, ".git", "hooks", "prepare-commit-msg")
        with open(hook_path, "w") as f:
            f.write(f"#!/bin/sh\nexec uv run --project '{PROJECT_DIR}' git-good hook \"$@\"\n")
        os.chmod(hook_path, 0o755)

        cast.blank()
        cast.comment("# Step 2: Make some changes to the code")
        cast.pause(0.8)

        new_code = '\n\ndef add(a, b):\n    """Add two numbers and return the result."""\n    return a + b\n\n\ndef subtract(a, b):\n    """Subtract b from a and return the result."""\n    return a - b\n'
        with open(app_py, "a") as f:
            f.write(new_code)

        cast.type_cmd("git diff app.py")
        diff_output = run(["git", "diff", "app.py"], cwd=repo_dir)
        for line in diff_output.strip().splitlines():
            cast.output(line)
        cast.pause(1.0)

        cast.blank()
        cast.comment("# Step 3: Stage changes and commit with the @@claude@@ placeholder")
        cast.pause(0.8)

        cast.type_cmd("git add app.py")
        run(["git", "add", "app.py"], cwd=repo_dir)
        cast.pause(0.5)

        cast.type_cmd("git commit -m '@@claude@@'")
        commit_output = run(
            ["git", "commit", "-m", "@@claude@@"],
            cwd=repo_dir,
            env=demo_env,
        )
        for line in commit_output.strip().splitlines():
            cast.output(line)
        cast.pause(1.0)

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
        mock_proc.kill()
        mock_proc.wait()
        shutil.rmtree(demo_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
