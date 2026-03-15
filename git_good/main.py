import argparse
import difflib
import os
import signal
import stat
import subprocess
import sys
import textwrap
import threading
import tomllib

import anthropic

PLACEHOLDER = "@@ai@@"

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

HOOK_SCRIPT = """\
#!/bin/sh
# git-good: prepare-commit-msg hook
# Replaces @@ai@@ in commit messages with AI-generated text
exec git-good hook "$@"
"""

GLOBAL_HOOK_SCRIPT = """\
#!/bin/sh
# git-good: prepare-commit-msg hook (global)
# Replaces @@ai@@ in commit messages with AI-generated text
# Then chains to repo-local hook if one exists
git-good hook "$@"
GIT_GOOD_EXIT=$?

# Chain to repo-local prepare-commit-msg hook if it exists
LOCAL_HOOK="$(git rev-parse --git-dir)/hooks/prepare-commit-msg"
if [ -x "$LOCAL_HOOK" ]; then
    "$LOCAL_HOOK" "$@"
    exit $?
fi

exit $GIT_GOOD_EXIT
"""

COMMIT_TEMPLATE = f"""\
{PLACEHOLDER}
"""

SYSTEM_PROMPT = textwrap.dedent("""\
    Provide a commit message for the currently staged changes.
    Provide overall summary as first line then bulleted list of high-level changes.
    Only print the message.
    - Use the imperative mood ("Add feature" not "Added feature")
    - Keep the first line under 72 characters
    - Do NOT wrap the message in quotes or markdown formatting
    - Output ONLY the commit message text, nothing else
""")


def get_repo_root():
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("error: not a git repository", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def _confirm_hook_overwrite(hook_path, existing, script):
    diff = difflib.unified_diff(
        existing.splitlines(keepends=True),
        script.splitlines(keepends=True),
        fromfile="existing prepare-commit-msg",
        tofile="new prepare-commit-msg",
    )
    RED = "\033[31m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    RESET = "\033[0m"
    print(f"\n{hook_path} already exists. Proposed changes:\n", file=sys.stderr)
    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            print(f"{CYAN}{line}{RESET}", end="", file=sys.stderr)
        elif line.startswith("-"):
            print(f"{RED}{line}{RESET}", end="", file=sys.stderr)
        elif line.startswith("+"):
            print(f"{GREEN}{line}{RESET}", end="", file=sys.stderr)
        else:
            print(line, end="", file=sys.stderr)
    print(file=sys.stderr)

    answer = input("Overwrite existing hook? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _install_hook(hooks_dir, script=HOOK_SCRIPT):
    hook_path = os.path.join(hooks_dir, "prepare-commit-msg")

    if os.path.exists(hook_path):
        with open(hook_path) as f:
            existing = f.read()
        if existing == script:
            print(f"Hook already installed at {hook_path}")
            return
        if not _confirm_hook_overwrite(hook_path, existing, script):
            print("Aborted.", file=sys.stderr)
            return

    os.makedirs(hooks_dir, exist_ok=True)
    with open(hook_path, "w") as f:
        f.write(script)
    os.chmod(hook_path, os.stat(hook_path).st_mode | stat.S_IEXEC)

    print(f"Installed prepare-commit-msg hook to {hook_path}")


def _get_hooks_dir(repo_root):
    """Get the effective hooks directory, respecting core.hooksPath if configured."""
    result = subprocess.run(
        ["git", "config", "core.hooksPath"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    if result.returncode == 0 and result.stdout.strip():
        hooks_path = result.stdout.strip()
        # Resolve relative paths against repo root
        if not os.path.isabs(hooks_path):
            hooks_path = os.path.join(repo_root, hooks_path)
        return hooks_path
    return os.path.join(repo_root, ".git", "hooks")


def cmd_install(args):
    if args.glob:
        cmd_install_global(args)
        return

    repo_root = get_repo_root()
    hooks_dir = _get_hooks_dir(repo_root)

    _install_hook(hooks_dir)

    # Create commit message template if one is not already configured
    result = subprocess.run(
        ["git", "config", "commit.template"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    if result.returncode == 0 and result.stdout.strip():
        print(f"Commit template already configured: {result.stdout.strip()}")
    else:
        template_rel = os.path.join(".git-commit-template")
        template_abs = os.path.join(repo_root, template_rel)
        with open(template_abs, "w") as f:
            f.write(COMMIT_TEMPLATE)
        subprocess.run(
            ["git", "config", "commit.template", template_rel],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        print(f"Created commit template at {template_rel}")


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "git-good")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.toml")
GLOBAL_HOOKS_DIR = os.path.join(CONFIG_DIR, "hooks")


def _install_alias(name, command):
    """Install a global git alias if not already set."""
    result = subprocess.run(
        ["git", "config", "--global", f"alias.{name}"],
        capture_output=True,
        text=True,
    )
    current = result.stdout.strip() if result.returncode == 0 else ""
    if current == command:
        print(f"Git alias '{name}' already configured")
        return
    if current:
        print(f"Warning: alias.{name} is already set to: {current}", file=sys.stderr)
        answer = input(f"Override with '{command}'? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print(f"Skipping alias.{name} configuration.", file=sys.stderr)
            return
    subprocess.run(
        ["git", "config", "--global", f"alias.{name}", command],
        check=True,
    )
    print(f"Installed git alias '{name}'")


ALIAS_GOOD = f'!git commit -m "{PLACEHOLDER}"'
ALIAS_YOLO = f'!git commit -m "{PLACEHOLDER}" && git push'


def cmd_install_global(args):
    _install_hook(GLOBAL_HOOKS_DIR, script=GLOBAL_HOOK_SCRIPT)

    # Set global core.hooksPath
    result = subprocess.run(
        ["git", "config", "--global", "core.hooksPath"],
        capture_output=True,
        text=True,
    )
    current = result.stdout.strip() if result.returncode == 0 else ""
    if current and current != GLOBAL_HOOKS_DIR:
        print(
            f"Warning: core.hooksPath is already set to {current}",
            file=sys.stderr,
        )
        answer = input(f"Override to {GLOBAL_HOOKS_DIR}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Skipping core.hooksPath configuration.", file=sys.stderr)
            return

    if current != GLOBAL_HOOKS_DIR:
        subprocess.run(
            ["git", "config", "--global", "core.hooksPath", GLOBAL_HOOKS_DIR],
            check=True,
        )
        print(f"Set global core.hooksPath to {GLOBAL_HOOKS_DIR}")
    else:
        print(f"Global core.hooksPath already set to {GLOBAL_HOOKS_DIR}")

    # Set global commit template
    result = subprocess.run(
        ["git", "config", "--global", "commit.template"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        print(f"Global commit template already configured: {result.stdout.strip()}")
    else:
        template_path = os.path.join(
            os.path.expanduser("~"), ".config", "git-good", ".git-commit-template"
        )
        with open(template_path, "w") as f:
            f.write(COMMIT_TEMPLATE)
        subprocess.run(
            ["git", "config", "--global", "commit.template", template_path],
            check=True,
        )
        print(f"Created global commit template at {template_path}")

    # Install git aliases
    _install_alias("good", ALIAS_GOOD)
    _install_alias("yolo", ALIAS_YOLO)


def cmd_yolo(args):
    _install_alias("yolo", ALIAS_YOLO)


def _claim_foreground():
    """Become the foreground process group so Ctrl+C only signals us, not git."""
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
        original_pgrp = os.tcgetpgrp(tty_fd)
        old_ttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.setpgrp()
        os.tcsetpgrp(tty_fd, os.getpgrp())
        signal.signal(signal.SIGTTOU, old_ttou)
        return tty_fd, original_pgrp
    except OSError:
        return None, None


def _restore_foreground(tty_fd, original_pgrp):
    """Restore the original foreground process group."""
    if tty_fd is None:
        return
    try:
        old_ttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(tty_fd, original_pgrp)
        signal.signal(signal.SIGTTOU, old_ttou)
    except OSError:
        pass
    finally:
        os.close(tty_fd)


def _get_staged_file_contents():
    """Get the full content of staged files for context."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    )
    files = [f for f in result.stdout.strip().split("\n") if f]

    contents = []
    for filepath in files:
        try:
            with open(filepath) as f:
                content = f.read()
            contents.append(f"=== {filepath} ===\n{content}")
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            pass

    return "\n\n".join(contents)


def _load_config():
    """Load config from ~/.config/git-good/config.toml, returning a dict."""
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def _get_api_key():
    """Get the API key from the config file, falling back to env var."""
    config = _load_config()
    return config.get("api_key")


def _run_api_with_spinner(diff, file_contents):
    """Call Anthropic API with a spinner, returning the commit message text.

    If the user sends SIGINT (Ctrl+C), the function returns None so the
    caller can continue gracefully.
    """
    result = {"text": None, "error": None}

    def call_api():
        try:
            client = anthropic.Anthropic(api_key=_get_api_key())
            user_message = f"<diff>\n{diff}\n</diff>"
            if file_contents:
                user_message = (
                    f"<files>\n{file_contents}\n</files>\n\n{user_message}"
                )

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                timeout=30.0,
            )
            result["text"] = response.content[0].text.strip()
        except Exception as e:
            result["error"] = str(e)

    thread = threading.Thread(target=call_api)
    thread.start()

    # Become the foreground process group so Ctrl+C during the spinner only
    # interrupts us (git-good), not the parent git process.
    tty_fd, original_pgrp = _claim_foreground()

    interrupted = False
    spinner_idx = 0
    try:
        while thread.is_alive():
            frame = SPINNER_FRAMES[spinner_idx % len(SPINNER_FRAMES)]
            print(
                f"\r{frame} Generating commit message...",
                end="",
                file=sys.stderr,
                flush=True,
            )
            spinner_idx += 1
            thread.join(timeout=0.1)
    except KeyboardInterrupt:
        interrupted = True
        thread.join(timeout=2)
    finally:
        # Clear the spinner line
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)
        _restore_foreground(tty_fd, original_pgrp)

    if interrupted:
        return None
    if result["error"]:
        raise RuntimeError(result["error"])
    return result["text"]


def cmd_hook(args):
    commit_msg_file = args.commit_msg_file

    with open(commit_msg_file) as f:
        message = f.read()

    if PLACEHOLDER not in message:
        return

    diff = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True,
        text=True,
    ).stdout

    if not diff.strip():
        print("git-good: no staged changes found, leaving placeholder", file=sys.stderr)
        return

    file_contents = _get_staged_file_contents()

    try:
        commit_msg = _run_api_with_spinner(diff, file_contents)

        if commit_msg is None:
            # Interrupted by user — continue the commit with placeholder
            print("git-good: interrupted, leaving placeholder as-is", file=sys.stderr)
            return

        if not commit_msg:
            raise RuntimeError("API returned empty response")
    except KeyboardInterrupt:
        print("git-good: interrupted, leaving placeholder as-is", file=sys.stderr)
        return
    except Exception as e:
        print(f"git-good: failed to generate message: {e}", file=sys.stderr)
        print("git-good: leaving placeholder as-is", file=sys.stderr)
        return

    message = message.replace(PLACEHOLDER, commit_msg)
    print()
    with open(commit_msg_file, "w") as f:
        f.write(message)


def main():
    parser = argparse.ArgumentParser(
        prog="git-good",
        description="AI-powered commit message generation via git hooks",
    )
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="Install the prepare-commit-msg hook")
    install_parser.add_argument(
        "--global",
        dest="glob",
        action="store_true",
        help="Install globally for all repos via core.hooksPath",
    )

    hook_parser = subparsers.add_parser("hook", help="Hook entrypoint (called by git)")
    hook_parser.add_argument("commit_msg_file")
    hook_parser.add_argument("source", nargs="?", default="")
    hook_parser.add_argument("sha", nargs="?", default="")

    subparsers.add_parser("yolo", help="Install 'git yolo' alias (commit with AI message + push)")

    args = parser.parse_args()

    if args.command == "install":
        cmd_install(args)
    elif args.command == "hook":
        cmd_hook(args)
    elif args.command == "yolo":
        cmd_yolo(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
