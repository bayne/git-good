import argparse
import difflib
import os
import signal
import stat
import subprocess
import sys
import textwrap
import threading
import time

PLACEHOLDER = "@@ai@@"

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

HOOK_SCRIPT = """\
#!/bin/sh
# git-good: prepare-commit-msg hook
# Replaces @@ai@@ in commit messages with AI-generated text
exec git-good hook "$@"
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


def cmd_install(args):
    repo_root = get_repo_root()
    hooks_dir = os.path.join(repo_root, ".git", "hooks")
    hook_path = os.path.join(hooks_dir, "prepare-commit-msg")

    if os.path.exists(hook_path):
        with open(hook_path) as f:
            existing = f.read()
        if existing == HOOK_SCRIPT:
            print(f"Hook already installed at {hook_path}")
        else:
            diff = difflib.unified_diff(
                existing.splitlines(keepends=True),
                HOOK_SCRIPT.splitlines(keepends=True),
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
            if answer not in ("y", "yes"):
                print("Aborted.", file=sys.stderr)
                return

    os.makedirs(hooks_dir, exist_ok=True)
    with open(hook_path, "w") as f:
        f.write(HOOK_SCRIPT)
    os.chmod(hook_path, os.stat(hook_path).st_mode | stat.S_IEXEC)

    print(f"Installed prepare-commit-msg hook to {hook_path}")

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
        template_rel = os.path.join(".git", "commit-template")
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


def _run_claude_with_spinner(prompt):
    """Run claude CLI with a spinner, returning (stdout, stderr, returncode).

    If the user sends SIGINT (Ctrl+C), the claude process is killed and
    the function returns None so the caller can continue gracefully.
    """
    proc = subprocess.Popen(
        ["claude", "--print", "--no-session-persistence", "--model", "haiku"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    result = {"stdout": "", "stderr": "", "returncode": None}

    def communicate():
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=30)
            result["stdout"] = stdout
            result["stderr"] = stderr
            result["returncode"] = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            result["returncode"] = -1
            result["stderr"] = "timed out after 30 seconds"

    thread = threading.Thread(target=communicate)
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
        proc.kill()
        thread.join()
        interrupted = True
    finally:
        # Clear the spinner line
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)
        _restore_foreground(tty_fd, original_pgrp)

    return None if interrupted else result


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

    prompt = f"{SYSTEM_PROMPT}\n\nGenerate a commit message for this diff:\n\n{diff}"

    try:
        result = _run_claude_with_spinner(prompt)

        if result is None:
            # Interrupted by user — continue the commit with placeholder
            print("git-good: interrupted, leaving placeholder as-is", file=sys.stderr)
            return

        if result["returncode"] != 0:
            raise RuntimeError(
                result["stderr"].strip()
                or f"claude exited with code {result['returncode']}"
            )
        commit_msg = result["stdout"].strip()
        if not commit_msg:
            raise RuntimeError("claude returned empty response")
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

    subparsers.add_parser("install", help="Install the prepare-commit-msg hook")

    hook_parser = subparsers.add_parser("hook", help="Hook entrypoint (called by git)")
    hook_parser.add_argument("commit_msg_file")
    hook_parser.add_argument("source", nargs="?", default="")
    hook_parser.add_argument("sha", nargs="?", default="")

    args = parser.parse_args()

    if args.command == "install":
        cmd_install(args)
    elif args.command == "hook":
        cmd_hook(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
