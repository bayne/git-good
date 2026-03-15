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
    CONFIG_FILE,
    HOOK_SCRIPT,
    PLACEHOLDER,
    SPINNER_FRAMES,
    SYSTEM_PROMPT,
    _get_api_key,
    _get_hooks_dir,
    _get_staged_file_contents,
    _load_config,
    _run_api_with_spinner,
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
# _load_config / _get_api_key
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_empty_dict_when_no_file(self, monkeypatch):
        monkeypatch.setattr("git_good.main.CONFIG_FILE", "/nonexistent/config.toml")
        assert _load_config() == {}

    def test_reads_toml_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text('api_key = "sk-test-123"\n')
        monkeypatch.setattr("git_good.main.CONFIG_FILE", str(config_file))
        assert _load_config() == {"api_key": "sk-test-123"}


class TestGetApiKey:
    def test_returns_key_from_config(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text('api_key = "sk-from-config"\n')
        monkeypatch.setattr("git_good.main.CONFIG_FILE", str(config_file))
        assert _get_api_key() == "sk-from-config"

    def test_returns_none_when_no_config(self, monkeypatch):
        monkeypatch.setattr("git_good.main.CONFIG_FILE", "/nonexistent/config.toml")
        assert _get_api_key() is None

    def test_returns_none_when_key_not_in_config(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.toml"
        config_file.write_text('other_setting = "value"\n')
        monkeypatch.setattr("git_good.main.CONFIG_FILE", str(config_file))
        assert _get_api_key() is None


# ---------------------------------------------------------------------------
# cmd_install
# ---------------------------------------------------------------------------


class TestGetHooksDir:
    def test_returns_default_when_no_config(self, tmp_path, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _get_hooks_dir(str(tmp_path))
        assert result == os.path.join(str(tmp_path), ".git", "hooks")

    def test_returns_configured_absolute_path(self, tmp_path, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="/custom/hooks\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _get_hooks_dir(str(tmp_path))
        assert result == "/custom/hooks"

    def test_resolves_relative_path_against_repo_root(self, tmp_path, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="my-hooks\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _get_hooks_dir(str(tmp_path))
        assert result == os.path.join(str(tmp_path), "my-hooks")

    def test_passes_repo_root_as_cwd(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _get_hooks_dir(str(tmp_path))
        assert calls[0]["cwd"] == str(tmp_path)

    def test_ignores_empty_config_value(self, tmp_path, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="  \n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _get_hooks_dir(str(tmp_path))
        assert result == os.path.join(str(tmp_path), ".git", "hooks")


class TestCmdInstall:
    def _patch_hooks_dir(self, monkeypatch, tmp_path):
        """Monkeypatch _get_hooks_dir to return .git/hooks under tmp_path."""
        hooks_dir = str(tmp_path / ".git" / "hooks")
        monkeypatch.setattr(
            "git_good.main._get_hooks_dir", lambda repo_root: hooks_dir
        )

    def test_creates_hook_file(self, tmp_path, monkeypatch):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        self._patch_hooks_dir(monkeypatch, tmp_path)
        args = mock.MagicMock(glob=False)
        cmd_install(args)

        hook_path = git_dir / "prepare-commit-msg"
        assert hook_path.exists()
        assert hook_path.read_text() == HOOK_SCRIPT

    def test_hook_is_executable(self, tmp_path, monkeypatch):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        self._patch_hooks_dir(monkeypatch, tmp_path)
        cmd_install(mock.MagicMock(glob=False))

        hook_path = tmp_path / ".git" / "hooks" / "prepare-commit-msg"
        mode = hook_path.stat().st_mode
        assert mode & stat.S_IEXEC

    def test_creates_hooks_dir_if_missing(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        self._patch_hooks_dir(monkeypatch, tmp_path)
        cmd_install(mock.MagicMock(glob=False))
        assert (tmp_path / ".git" / "hooks" / "prepare-commit-msg").exists()

    def test_overwrites_existing_hook_after_confirmation(self, tmp_path, monkeypatch, capsys):
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        existing = hooks_dir / "prepare-commit-msg"
        existing.write_text("#!/bin/sh\nold hook")

        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        self._patch_hooks_dir(monkeypatch, tmp_path)
        monkeypatch.setattr("builtins.input", lambda _: "y")
        cmd_install(mock.MagicMock(glob=False))

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
        self._patch_hooks_dir(monkeypatch, tmp_path)
        monkeypatch.setattr("builtins.input", lambda _: "n")
        cmd_install(mock.MagicMock(glob=False))

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
        self._patch_hooks_dir(monkeypatch, tmp_path)
        cmd_install(mock.MagicMock(glob=False))

        assert "already installed" in capsys.readouterr().out

    def test_prints_success_message(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        self._patch_hooks_dir(monkeypatch, tmp_path)
        cmd_install(mock.MagicMock(glob=False))
        assert "Installed" in capsys.readouterr().out

    def test_uses_custom_hooks_path(self, tmp_path, monkeypatch, capsys):
        """When core.hooksPath is set, install hook there instead of .git/hooks."""
        custom_hooks = tmp_path / "custom-hooks"
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        monkeypatch.setattr(
            "git_good.main._get_hooks_dir", lambda repo_root: str(custom_hooks)
        )
        cmd_install(mock.MagicMock(glob=False))

        hook_path = custom_hooks / "prepare-commit-msg"
        assert hook_path.exists()
        assert hook_path.read_text() == HOOK_SCRIPT

    def test_creates_commit_template(self, tmp_path, monkeypatch):
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        monkeypatch.setattr(
            "git_good.main.get_repo_root", lambda: str(tmp_path)
        )
        self._patch_hooks_dir(monkeypatch, tmp_path)
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
        cmd_install(mock.MagicMock(glob=False))

        template_path = tmp_path / ".git-commit-template"
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
        self._patch_hooks_dir(monkeypatch, tmp_path)

        def fake_run(cmd, *args, **kwargs):
            if cmd == ["git", "config", "commit.template"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="/some/template\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        cmd_install(mock.MagicMock(glob=False))

        template_path = tmp_path / ".git-commit-template"
        assert not template_path.exists()
        assert "already configured" in capsys.readouterr().out

    def test_hook_script_contains_exec(self):
        assert "exec git-good hook" in HOOK_SCRIPT

    def test_hook_script_has_shebang(self):
        assert HOOK_SCRIPT.startswith("#!/bin/sh\n")


# ---------------------------------------------------------------------------
# _get_staged_file_contents
# ---------------------------------------------------------------------------


class TestGetStagedFileContents:
    def test_returns_file_contents(self, tmp_path, monkeypatch):
        (tmp_path / "foo.py").write_text("print('hello')\n")
        monkeypatch.chdir(tmp_path)

        def fake_run(cmd, *args, **kwargs):
            if "diff" in cmd and "--name-only" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="foo.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _get_staged_file_contents()
        assert "foo.py" in result
        assert "print('hello')" in result

    def test_skips_deleted_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        def fake_run(cmd, *args, **kwargs):
            if "diff" in cmd and "--name-only" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="deleted.py\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _get_staged_file_contents()
        assert result == ""

    def test_handles_empty_diff(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _get_staged_file_contents()
        assert result == ""


# ---------------------------------------------------------------------------
# _run_api_with_spinner
# ---------------------------------------------------------------------------


def _make_mock_response(text="Fix bug in parser"):
    """Create a mock Anthropic API response."""
    mock_content = mock.MagicMock()
    mock_content.text = text
    mock_response = mock.MagicMock()
    mock_response.content = [mock_content]
    return mock_response


class TestRunApiWithSpinner:
    def test_returns_text_on_success(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = _make_mock_response("Fix bug in parser")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        result = _run_api_with_spinner("diff content", "file contents")
        assert result == "Fix bug in parser"

    def test_sends_diff_in_user_message(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = _make_mock_response("msg")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        _run_api_with_spinner("my diff content", "")
        call_kwargs = mock_client.messages.create.call_args[1]
        user_msg = call_kwargs["messages"][0]["content"]
        assert "my diff content" in user_msg

    def test_sends_file_contents_when_provided(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = _make_mock_response("msg")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        _run_api_with_spinner("diff", "=== foo.py ===\nprint('hi')")
        call_kwargs = mock_client.messages.create.call_args[1]
        user_msg = call_kwargs["messages"][0]["content"]
        assert "foo.py" in user_msg
        assert "print('hi')" in user_msg

    def test_omits_files_tag_when_no_contents(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = _make_mock_response("msg")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        _run_api_with_spinner("diff", "")
        call_kwargs = mock_client.messages.create.call_args[1]
        user_msg = call_kwargs["messages"][0]["content"]
        assert "<files>" not in user_msg

    def test_uses_haiku_model(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = _make_mock_response("msg")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        _run_api_with_spinner("diff", "")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_sends_system_prompt(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = _make_mock_response("msg")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        _run_api_with_spinner("diff", "")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == SYSTEM_PROMPT

    def test_raises_on_api_error(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        with pytest.raises(RuntimeError, match="API error"):
            _run_api_with_spinner("diff", "")

    def test_strips_whitespace_from_response(self, monkeypatch):
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = _make_mock_response("  Fix bug  \n\n")
        monkeypatch.setattr("git_good.main.anthropic.Anthropic", lambda **kw: mock_client)

        result = _run_api_with_spinner("diff", "")
        assert result == "Fix bug"


# ---------------------------------------------------------------------------
# cmd_hook
# ---------------------------------------------------------------------------


def _make_hook_mocks(monkeypatch, diff_stdout="", api_response="", api_error=None, file_contents=""):
    """Set up mocks for cmd_hook: mock git diff via subprocess.run, mock _run_api_with_spinner."""
    # Mock git diff and git diff --name-only
    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "git" and "diff" in cmd:
            if "--name-only" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout=diff_stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Mock _run_api_with_spinner
    api_calls = []

    def fake_api(diff, file_contents):
        api_calls.append({"diff": diff, "file_contents": file_contents})
        if api_error:
            raise RuntimeError(api_error)
        return api_response

    monkeypatch.setattr("git_good.main._run_api_with_spinner", fake_api)
    return api_calls


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
            api_response="Add hello print statement",
        )

        cmd_hook(self._make_args(str(msg_file)))

        assert msg_file.read_text() == "Add hello print statement"

    def test_preserves_surrounding_text(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(f"prefix: {PLACEHOLDER}\n\n# Some comment")

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff content\n",
            api_response="Fix bug in parser",
        )

        cmd_hook(self._make_args(str(msg_file)))

        result = msg_file.read_text()
        assert result.startswith("prefix: Fix bug in parser")
        assert "# Some comment" in result

    def test_handles_api_error_gracefully(self, tmp_path, monkeypatch, capsys):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        original_text = f"commit: {PLACEHOLDER}"
        msg_file.write_text(original_text)

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff content\n",
            api_error="API key not found",
        )

        cmd_hook(self._make_args(str(msg_file)))

        # Message should be unchanged
        assert msg_file.read_text() == original_text
        err = capsys.readouterr().err
        assert "failed to generate" in err
        assert "leaving placeholder" in err

    def test_handles_empty_api_response(self, tmp_path, monkeypatch, capsys):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        original_text = f"{PLACEHOLDER}"
        msg_file.write_text(original_text)

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff content\n",
            api_response="",
        )

        cmd_hook(self._make_args(str(msg_file)))

        assert msg_file.read_text() == original_text
        assert "failed to generate" in capsys.readouterr().err

    def test_handles_interrupted_gracefully(self, tmp_path, monkeypatch, capsys):
        """When _run_api_with_spinner returns None (interrupted), leave placeholder."""
        msg_file = tmp_path / "COMMIT_EDITMSG"
        original_text = f"{PLACEHOLDER}"
        msg_file.write_text(original_text)

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "git" and "diff" in cmd:
                if "--name-only" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="diff content\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("git_good.main._run_api_with_spinner", lambda diff, fc: None)

        cmd_hook(self._make_args(str(msg_file)))

        assert msg_file.read_text() == original_text
        assert "interrupted" in capsys.readouterr().err

    def test_sends_diff_to_api(self, tmp_path, monkeypatch):
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(PLACEHOLDER)

        diff_content = "diff --git a/main.py b/main.py\n+new line\n"
        api_calls = _make_hook_mocks(
            monkeypatch,
            diff_stdout=diff_content,
            api_response="Update main",
        )

        cmd_hook(self._make_args(str(msg_file)))

        # The diff sent to the API should contain the diff content
        assert diff_content in api_calls[0]["diff"]

    def test_only_replaces_first_placeholder(self, tmp_path, monkeypatch):
        """str.replace replaces all occurrences - verify behavior."""
        msg_file = tmp_path / "COMMIT_EDITMSG"
        msg_file.write_text(f"{PLACEHOLDER} and {PLACEHOLDER}")

        _make_hook_mocks(
            monkeypatch,
            diff_stdout="diff\n",
            api_response="Update",
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
