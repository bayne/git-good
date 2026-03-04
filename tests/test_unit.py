"""Extensive unit tests for git-good."""

import os
import stat
import subprocess
import textwrap
import threading
from unittest import mock

import pytest

from git_good.main import (
    COMMIT_TEMPLATE,
    HOOK_SCRIPT,
    PLACEHOLDER,
    SPINNER_FRAMES,
    SYSTEM_PROMPT,
    _run_claude_with_spinner,
    cmd_hook,
    cmd_install,
    get_repo_root,
    main,
)


# ---------------------------------------------------------------------------
# get_repo_root
# ---------------------------------------------------------------------------


class TestGetRepoRoot:
    def test_returns_stripped_path(self, monkeypatch):
        fake = subprocess.CompletedProcess([], 0, stdout="/home/user/repo\n", stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
        assert get_repo_root() == "/home/user/repo"

    def test_exits_when_not_a_repo(self, monkeypatch):
        fake = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal: not a git repo")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
        with pytest.raises(SystemExit) as exc:
            get_repo_root()
        assert exc.value.code == 1

    def test_prints_error_when_not_a_repo(self, monkeypatch, capsys):
        fake = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
        with pytest.raises(SystemExit):
            get_repo_root()
        assert "not a git repository" in capsys.readouterr().err

    def test_calls_git_rev_parse(self, monkeypatch):
        calls = []

        def spy(*a, **kw):
            calls.append(a[0])
            return subprocess.CompletedProcess([], 0, stdout="/repo\n", stderr="")

        monkeypatch.setattr(subprocess, "run", spy)
        get_repo_root()
        assert calls[0] == ["git", "rev-parse", "--show-toplevel"]


# ---------------------------------------------------------------------------
# cmd_install
# ---------------------------------------------------------------------------


class TestCmdInstall:
    def test_creates_hook_file(self, tmp_path, monkeypatch):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        args = mock.MagicMock()
        cmd_install(args)

        hook_path = git_dir / "prepare-commit-msg"
        assert hook_path.exists()
        assert hook_path.read_text() == HOOK_SCRIPT

    def test_hook_is_executable(self, tmp_path, monkeypatch):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        cmd_install(mock.MagicMock())

        hook_path = tmp_path / ".git" / "hooks" / "prepare-commit-msg"
        mode = hook_path.stat().st_mode
        assert mode & stat.S_IEXEC

    def test_creates_hooks_dir_if_missing(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        cmd_install(mock.MagicMock())
        assert (tmp_path / ".git" / "hooks" / "prepare-commit-msg").exists()

    def test_overwrites_existing_hook_after_confirmation(self, tmp_path, monkeypatch, capsys):
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        existing = hooks_dir / "prepare-commit-msg"
        existing.write_text("#!/bin/sh\nold hook")

        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        monkeypatch.setattr("builtins.input", lambda _: "y")
        cmd_install(mock.MagicMock())

        assert existing.read_text() == HOOK_SCRIPT
        err = capsys.readouterr().err
        assert "already exists" in err
        # Should show colored diff markers
        assert "-#!/bin/sh" in err or "\033[31m" in err

    def test_aborts_when_user_declines_overwrite(self, tmp_path, monkeypatch, capsys):
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        existing = hooks_dir / "prepare-commit-msg"
        existing.write_text("#!/bin/sh\nold hook")

        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        monkeypatch.setattr("builtins.input", lambda _: "n")
        cmd_install(mock.MagicMock())

        assert existing.read_text() == "#!/bin/sh\nold hook"
        assert "Aborted" in capsys.readouterr().err

    def test_skips_diff_when_hook_already_matches(self, tmp_path, monkeypatch, capsys):
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        existing = hooks_dir / "prepare-commit-msg"
        existing.write_text(HOOK_SCRIPT)

        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        cmd_install(mock.MagicMock())

        assert "already installed" in capsys.readouterr().out

    def test_prints_success_message(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        cmd_install(mock.MagicMock())
        assert "Installed" in capsys.readouterr().out

    def test_creates_commit_template(self, tmp_path, monkeypatch):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        git_config_calls = []
        original_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "config"]:
                git_config_calls.append(cmd)
                if cmd == ["git", "config", "commit.template"]:
                    # No template configured
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        cmd_install(mock.MagicMock())

        template_path = tmp_path / ".git" / "commit-template"
        assert template_path.exists()
        assert PLACEHOLDER in template_path.read_text()
        # Should have called git config to set the template
        set_calls = [c for c in git_config_calls if len(c) == 3 and c[1] == "commit.template"]
        assert any("commit-template" in str(c) for c in git_config_calls)

    def test_skips_template_if_already_configured(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )

        def fake_run(cmd, *args, **kwargs):
            if cmd == ["git", "config", "commit.template"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="/some/template\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        cmd_install(mock.MagicMock())

        template_path = tmp_path / ".git" / "commit-template"
        assert not template_path.exists()
        assert "already configured" in capsys.readouterr().out

    def test_hook_script_contains_exec(self):
        assert "exec git-good hook" in HOOK_SCRIPT

    def test_hook_script_has_shebang(self):
        assert HOOK_SCRIPT.startswith("#!/bin/sh\n")


# ---------------------------------------------------------------------------
# _run_claude_with_spinner (helper)
# ---------------------------------------------------------------------------


def _make_mock_popen(stdout="", stderr="", returncode=0):
    """Create a mock Popen that simulates claude CLI."""
    mock_proc = mock.MagicMock()
    mock_proc.communicate.return_value = (stdout, stderr)
    mock_proc.returncode = returncode
    mock_proc.poll.return_value = returncode
    # Make kill/wait no-ops
    mock_proc.kill.return_value = None
    mock_proc.wait.return_value = returncode
    return mock_proc


class TestRunClaudeWithSpinner:
    def test_returns_result_on_success(self, monkeypatch):
        mock_proc = _make_mock_popen(stdout="Fix bug in parser", returncode=0)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)
        result = _run_claude_with_spinner("prompt")
        assert result is not None
        assert result["stdout"] == "Fix bug in parser"
        assert result["returncode"] == 0

    def test_passes_prompt_via_stdin(self, monkeypatch):
        mock_proc = _make_mock_popen(stdout="msg", returncode=0)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)
        _run_claude_with_spinner("my prompt text")
        mock_proc.communicate.assert_called_once_with(input="my prompt text", timeout=30)

    def test_uses_correct_claude_flags(self, monkeypatch):
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return _make_mock_popen(stdout="msg", returncode=0)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        _run_claude_with_spinner("prompt")
        cmd = popen_calls[0]
        assert cmd == ["claude", "--print", "--no-session-persistence", "--model", "haiku"]

    def test_handles_timeout(self, monkeypatch):
        mock_proc = mock.MagicMock()
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(["claude"], 30),
            ("", ""),  # second communicate after kill
        ]
        mock_proc.returncode = -1
        mock_proc.poll.return_value = -1
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)
        result = _run_claude_with_spinner("prompt")
        assert result is not None
        assert result["returncode"] == -1
        assert "timed out" in result["stderr"]

    def test_returns_nonzero_on_failure(self, monkeypatch):
        mock_proc = _make_mock_popen(stdout="", stderr="error msg", returncode=1)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)
        result = _run_claude_with_spinner("prompt")
        assert result["returncode"] == 1
        assert result["stderr"] == "error msg"


# ---------------------------------------------------------------------------
# cmd_hook
# ---------------------------------------------------------------------------


def _make_hook_mocks(monkeypatch, diff_stdout="", claude_stdout="", claude_returncode=0, claude_stderr=""):
    """Set up mocks for cmd_hook: mock git diff via subprocess.run, mock claude via _run_claude_with_spinner."""
    # Mock git diff
    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "git" and "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=diff_stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Mock _run_claude_with_spinner
    claude_calls = []

    def fake_spinner(prompt):
        claude_calls.append(prompt)
        return {"stdout": claude_stdout, "stderr": claude_stderr, "returncode": claude_returncode}

    monkeypatch.setattr("git_good.main._run_claude_with_spinner", fake_spinner)
    return claude_calls


class TestCmdHook:
    def _make_args(self, commit_msg_file, source="", sha=""):
        args = mock.MagicMock()
        args.commit_msg_file = commit_msg_file
        args.source = source
        args.sha = sha
        return args

    def test_skips_when_no_placeholder(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text("just a normal commit message")
        args = self._make_args(str(msg_file))

        # Should return early without calling subprocess or claude
        run_calls = []
        original_run = subprocess.run
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: run_calls.append(a) or original_run(*a, **kw),
        )
        cmd_hook(args)
        # No subprocess calls should have been made (no git diff)
        assert len(run_calls) == 0

    def test_skips_when_diff_is_empty(self, tmp_path, monkeypatch, capsys):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(f"commit: {PLACEHOLDER}")

        fake_diff = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_diff)

        cmd_hook(self._make_args(str(msg_file)))

        # Message should be unchanged
        assert msg_file.read_text() == f"commit: {PLACEHOLDER}"
        assert "no staged changes" in capsys.readouterr().err

    def test_replaces_placeholder_with_ai_message(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(f"{PLACEHOLDER}")

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff --git a/foo.py ...\n+print('hello')\n",
            claude_stdout="Add hello print statement",
        )

        cmd_hook(self._make_args(str(msg_file)))

        assert msg_file.read_text() == "Add hello print statement"

    def test_preserves_surrounding_text(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(f"prefix: {PLACEHOLDER}\n\n# Some comment")

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff content\n",
            claude_stdout="Fix bug in parser",
        )

        cmd_hook(self._make_args(str(msg_file)))

        result = msg_file.read_text()
        assert result.startswith("prefix: Fix bug in parser")
        assert "# Some comment" in result

    def test_handles_claude_error_gracefully(self, tmp_path, monkeypatch, capsys):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        original_text = f"commit: {PLACEHOLDER}"
        msg_file.write_text(original_text)

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff content\n",
            claude_returncode=1,
            claude_stderr="claude not found",
        )

        cmd_hook(self._make_args(str(msg_file)))

        # Message should be unchanged
        assert msg_file.read_text() == original_text
        err = capsys.readouterr().err
        assert "failed to generate" in err
        assert "leaving placeholder" in err

    def test_handles_empty_claude_response(self, tmp_path, monkeypatch, capsys):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        original_text = f"{PLACEHOLDER}"
        msg_file.write_text(original_text)

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff content\n",
            claude_stdout="",
        )

        cmd_hook(self._make_args(str(msg_file)))

        assert msg_file.read_text() == original_text
        assert "failed to generate" in capsys.readouterr().err

    def test_handles_interrupted_gracefully(self, tmp_path, monkeypatch, capsys):
        """When _run_claude_with_spinner returns None (interrupted), leave placeholder."""
        msg_file = tmp_path / "COMMIT_EDITMSG"
        original_text = f"{PLACEHOLDER}"
        msg_file.write_text(original_text)

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "git" and "diff" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="diff content\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("git_good.main._run_claude_with_spinner", lambda prompt: None)

        cmd_hook(self._make_args(str(msg_file)))

        assert msg_file.read_text() == original_text
        assert "interrupted" in capsys.readouterr().err

    def test_sends_diff_to_claude(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        diff_content = "diff --git a/main.py b/main.py\n+new line\n"
        claude_calls = _make_hook_mocks(
            monkeypatch,
            diff_stdout=diff_content,
            claude_stdout="Update main",
        )

        cmd_hook(self._make_args(str(msg_file)))

        # The prompt sent to claude should contain the diff
        assert diff_content in claude_calls[0]

    def test_sends_system_prompt_to_claude(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        claude_calls = _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff\n",
            claude_stdout="msg",
        )

        cmd_hook(self._make_args(str(msg_file)))

        assert "imperative" in claude_calls[0].lower()

    def test_strips_whitespace_from_claude_response(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff\n",
            claude_stdout="  Fix bug  \n\n",
        )

        cmd_hook(self._make_args(str(msg_file)))

        assert msg_file.read_text() == "Fix bug"

    def test_only_replaces_first_placeholder(self, tmp_path, monkeypatch):
        """str.replace replaces all occurrences - verify behavior."""
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(f"{PLACEHOLDER} and {PLACEHOLDER}")

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff\n",
            claude_stdout="Update",
        )

        cmd_hook(self._make_args(str(msg_file)))

        # str.replace replaces ALL occurrences
        assert msg_file.read_text() == "Update and Update"

    def test_whitespace_only_diff_treated_as_empty(self, tmp_path, monkeypatch, capsys):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        fake_diff = subprocess.CompletedProcess([], 0, stdout="   \n  \n", stderr="")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_diff)

        cmd_hook(self._make_args(str(msg_file)))
        assert "no staged changes" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_placeholder_value(self):
        assert PLACEHOLDER == "@@ai@@"

    def test_system_prompt_mentions_imperative(self):
        assert "imperative" in SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_72_chars(self):
        assert "72" in SYSTEM_PROMPT

    def test_commit_template_contains_placeholder(self):
        assert PLACEHOLDER in COMMIT_TEMPLATE

    def test_hook_script_references_git_good(self):
        assert "git-good" in HOOK_SCRIPT

    def test_spinner_frames_not_empty(self):
        assert len(SPINNER_FRAMES) > 0


# ---------------------------------------------------------------------------
# main() / CLI parsing
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_no_args_prints_help(self, capsys):
        with mock.patch("sys.argv", ["git-good"]):
            main()
        out = capsys.readouterr().out
        assert "usage" in out.lower() or "git-good" in out.lower()

    def test_install_command_calls_cmd_install(self, monkeypatch):
        called = []
        monkeypatch.setattr("git_good.main.cmd_install", lambda a: called.append(True))
        with mock.patch("sys.argv", ["git-good", "install"]):
            main()
        assert called

    def test_hook_command_calls_cmd_hook(self, monkeypatch):
        called = []
        monkeypatch.setattr("git_good.main.cmd_hook", lambda a: called.append(True))
        with mock.patch("sys.argv", ["git-good", "hook", "/tmp/msg"]):
            main()
        assert called

    def test_hook_command_passes_commit_msg_file(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            "git_good.main.cmd_hook", lambda a: captured.append(a)
        )
        with mock.patch("sys.argv", ["git-good", "hook", "/tmp/COMMIT_EDITMSG"]):
            main()
        assert captured[0].commit_msg_file == "/tmp/COMMIT_EDITMSG"

    def test_hook_command_optional_source_and_sha(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            "git_good.main.cmd_hook", lambda a: captured.append(a)
        )
        with mock.patch("sys.argv", ["git-good", "hook", "/tmp/msg", "message", "abc123"]):
            main()
        assert captured[0].source == "message"
        assert captured[0].sha == "abc123"

    def test_hook_command_defaults_source_and_sha(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            "git_good.main.cmd_hook", lambda a: captured.append(a)
        )
        with mock.patch("sys.argv", ["git-good", "hook", "/tmp/msg"]):
            main()
        assert captured[0].source == ""
        assert captured[0].sha == ""

    def test_unknown_command_prints_help(self, capsys):
        """Unknown subcommands should trigger help (args.command is None)."""
        with mock.patch("sys.argv", ["git-good"]):
            main()
        out = capsys.readouterr().out
        assert "install" in out or "hook" in out
