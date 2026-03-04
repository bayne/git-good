"""Functional / integration tests for git-good happy path.

These tests exercise the real functions against actual temporary git
repositories, mocking only the claude CLI call (via _run_claude_with_spinner).
"""

import os
import stat
import subprocess
from unittest import mock

import pytest

from git_good.main import COMMIT_TEMPLATE, PLACEHOLDER, cmd_hook, cmd_install


@pytest.fixture()
def git_repo(tmp_path, monkeypatch):
    """Create a temporary git repo, cd into it, and return its path."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _mock_claude_spinner(response_text):
    """Return a monkeypatch-compatible _run_claude_with_spinner replacement."""
    mock_calls = []

    def fake_spinner(prompt):
        mock_calls.append(prompt)
        return {"stdout": response_text, "stderr": "", "returncode": 0}

    return fake_spinner, mock_calls


class TestInstallFunctional:
    """End-to-end tests for `git-good install`."""

    def test_install_creates_executable_hook(self, git_repo):
        cmd_install(mock.MagicMock())

        hook = git_repo / ".git" / "hooks" / "prepare-commit-msg"
        assert hook.exists()
        assert hook.stat().st_mode & stat.S_IEXEC
        assert "git-good hook" in hook.read_text()

    def test_install_prints_success(self, git_repo, capsys):
        cmd_install(mock.MagicMock())
        assert "Installed" in capsys.readouterr().out

    def test_install_twice_skips_when_identical(self, git_repo, capsys):
        cmd_install(mock.MagicMock())
        capsys.readouterr()  # clear first output
        cmd_install(mock.MagicMock())
        assert "already installed" in capsys.readouterr().out

    def test_install_creates_commit_template(self, git_repo, capsys):
        cmd_install(mock.MagicMock())
        template_path = git_repo / ".git" / "commit-template"
        assert template_path.exists()
        assert PLACEHOLDER in template_path.read_text()
        assert "Created commit template" in capsys.readouterr().out

    def test_install_skips_template_if_configured(self, git_repo, capsys):
        # Pre-configure a commit template
        subprocess.run(
            ["git", "config", "commit.template", "/some/existing/template"],
            cwd=git_repo, capture_output=True, check=True,
        )
        cmd_install(mock.MagicMock())
        template_path = git_repo / ".git" / "commit-template"
        assert not template_path.exists()
        assert "already configured" in capsys.readouterr().out


class TestHookFunctional:
    """End-to-end tests for `git-good hook` with real git repos."""

    def test_hook_no_placeholder_leaves_message(self, git_repo):
        msg_file = git_repo / "COMMIT_EDITMSG"
        msg_file.write_text("Normal commit message")

        args = mock.MagicMock()
        args.commit_msg_file = str(msg_file)
        cmd_hook(args)

        assert msg_file.read_text() == "Normal commit message"

    def test_hook_empty_diff_leaves_placeholder(self, git_repo, capsys):
        msg_file = git_repo / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        args = mock.MagicMock()
        args.commit_msg_file = str(msg_file)
        cmd_hook(args)

        assert msg_file.read_text() == PLACEHOLDER
        assert "no staged changes" in capsys.readouterr().err

    def test_hook_replaces_placeholder_with_staged_changes(self, git_repo, monkeypatch):
        """Happy path: stage a file, run hook, placeholder gets replaced."""
        (git_repo / "hello.py").write_text("print('hello world')\n")
        subprocess.run(["git", "add", "hello.py"], cwd=git_repo, capture_output=True, check=True)

        msg_file = git_repo / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        fake_spinner, _ = _mock_claude_spinner("Add hello world script")
        monkeypatch.setattr("git_good.main._run_claude_with_spinner", fake_spinner)

        args = mock.MagicMock()
        args.commit_msg_file = str(msg_file)
        cmd_hook(args)

        assert msg_file.read_text() == "Add hello world script"

    def test_hook_sends_real_diff_content(self, git_repo, monkeypatch):
        """Verify the actual staged diff is sent to claude."""
        (git_repo / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "app.py"], cwd=git_repo, capture_output=True, check=True)

        msg_file = git_repo / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        fake_spinner, mock_calls = _mock_claude_spinner("Add app module")
        monkeypatch.setattr("git_good.main._run_claude_with_spinner", fake_spinner)

        args = mock.MagicMock()
        args.commit_msg_file = str(msg_file)
        cmd_hook(args)

        # The prompt sent to claude should contain the diff content
        prompt = mock_calls[0]
        assert "app.py" in prompt
        assert "x = 1" in prompt

    def test_hook_with_multiple_staged_files(self, git_repo, monkeypatch):
        """Verify hook works when multiple files are staged."""
        (git_repo / "a.py").write_text("a = 1\n")
        (git_repo / "b.py").write_text("b = 2\n")
        subprocess.run(["git", "add", "a.py", "b.py"], cwd=git_repo, capture_output=True, check=True)

        msg_file = git_repo / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        fake_spinner, mock_calls = _mock_claude_spinner("Add initial modules")
        monkeypatch.setattr("git_good.main._run_claude_with_spinner", fake_spinner)

        args = mock.MagicMock()
        args.commit_msg_file = str(msg_file)
        cmd_hook(args)

        assert msg_file.read_text() == "Add initial modules"
        prompt = mock_calls[0]
        assert "a.py" in prompt
        assert "b.py" in prompt


class TestFullWorkflowFunctional:
    """Test the complete install -> commit workflow."""

    def test_install_then_hook_invocation(self, git_repo, monkeypatch):
        """Install the hook, then simulate what git does when committing."""
        # Step 1: Install
        cmd_install(mock.MagicMock())
        assert (git_repo / ".git" / "hooks" / "prepare-commit-msg").exists()

        # Step 2: Stage a file
        (git_repo / "app.py").write_text("def main():\n    pass\n")
        subprocess.run(["git", "add", "app.py"], cwd=git_repo, capture_output=True, check=True)

        # Step 3: Simulate git calling the hook
        msg_file = git_repo / ".git" / "COMMIT_EDITMSG"
        msg_file.write_text(f"{PLACEHOLDER}\n\n# Comments from git")

        fake_spinner, _ = _mock_claude_spinner("Add main application entry point")
        monkeypatch.setattr("git_good.main._run_claude_with_spinner", fake_spinner)

        args = mock.MagicMock()
        args.commit_msg_file = str(msg_file)
        cmd_hook(args)

        content = msg_file.read_text()
        assert "Add main application entry point" in content
        assert "# Comments from git" in content
        assert PLACEHOLDER not in content

    def test_full_cycle_with_initial_commit(self, git_repo, monkeypatch):
        """Full cycle: install, create file, stage, and verify hook processes it."""
        # Install hook
        cmd_install(mock.MagicMock())

        # Make initial commit (temporarily remove hook so git commit works)
        hook_path = git_repo / ".git" / "hooks" / "prepare-commit-msg"
        hook_content = hook_path.read_text()
        hook_path.unlink()

        (git_repo / "README").write_text("initial\n")
        subprocess.run(["git", "add", "README"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=git_repo, capture_output=True, check=True,
        )

        # Restore hook
        hook_path.write_text(hook_content)

        # Stage changes
        (git_repo / "README").write_text("updated\n")
        subprocess.run(["git", "add", "README"], cwd=git_repo, capture_output=True, check=True)

        msg_file = git_repo / ".git" / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        fake_spinner, mock_calls = _mock_claude_spinner("Update README content")
        monkeypatch.setattr("git_good.main._run_claude_with_spinner", fake_spinner)

        args = mock.MagicMock()
        args.commit_msg_file = str(msg_file)
        cmd_hook(args)

        assert msg_file.read_text() == "Update README content"
        # Verify the diff was about the README change
        prompt = mock_calls[0]
        assert "README" in prompt
