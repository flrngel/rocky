Status: DONE
"""Tests for O12 - shell tool argv[0] tool-name guard.

When the LLM tries to invoke a rocky tool name as a CLI command
(e.g. run_shell_command("search_web '...'")) the shell tool must
detect this before subprocess.run and return a structured
{"error": "tool_name_in_shell", ...} ToolResult rather than
running the subprocess.
"""

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(tmp_path):
    from rocky.app import RockyRuntime
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"
    return runtime.tool_registry.context


# ---------------------------------------------------------------------------
# 1. Blocked tool name as argv[0]
# ---------------------------------------------------------------------------

class TestToolNameAsArgv0:
    def test_search_web_as_argv0_is_blocked(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "search_web 'compare pricing tables'"})

        assert result.success is False
        assert result.data["error"] == "tool_name_in_shell"
        assert result.data["tool"] == "search_web"

    def test_search_web_reroute_to_is_non_empty(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "search_web 'compare pricing tables'"})

        reroute = result.data.get("reroute_to")
        assert isinstance(reroute, str) and len(reroute) > 0

    def test_search_web_subprocess_not_spawned(self, tmp_path):
        """Guard fires before subprocess; stdout/stderr keys must be absent."""
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "search_web 'compare pricing tables'"})

        assert result.data.get("stdout") is None
        assert result.data.get("stderr") is None

    def test_read_file_as_argv0_is_blocked(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "read_file /etc/hosts"})

        assert result.success is False
        assert result.data["error"] == "tool_name_in_shell"
        assert result.data["tool"] == "read_file"

    def test_run_shell_command_as_argv0_is_blocked(self, tmp_path):
        """Even calling run_shell_command as a CLI must be blocked."""
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "run_shell_command echo hi"})

        assert result.success is False
        assert result.data["error"] == "tool_name_in_shell"
        assert result.data["tool"] == "run_shell_command"


# ---------------------------------------------------------------------------
# 2. Negative control - tool name in argv[1+] is fine
# ---------------------------------------------------------------------------

class TestToolNameInLaterArgv:
    def test_grep_with_tool_name_as_pattern_is_allowed(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        # argv[0] == "grep", not a tool name; must NOT trigger the guard
        result = run_shell_command(ctx, {"command": "grep search_web /dev/null"})

        assert result.data.get("error") != "tool_name_in_shell"

    def test_echo_search_web_is_allowed(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "echo search_web"})

        assert result.data.get("error") != "tool_name_in_shell"
        assert result.success is True
        assert "search_web" in result.data["stdout"]


# ---------------------------------------------------------------------------
# 3. Normal command passes through
# ---------------------------------------------------------------------------

class TestNormalCommand:
    def test_echo_hello_succeeds(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "echo hello"})

        assert result.success is True
        assert "hello" in result.data["stdout"]


# ---------------------------------------------------------------------------
# 4. O15 regression - env command must still be blocked by the env-blocklist
# ---------------------------------------------------------------------------

class TestEnvBlocklistRegression:
    def test_env_command_still_returns_blocked_verification_command(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "env"})

        assert result.success is False
        # O15 error shape must be preserved; O12 must not shadow it
        assert result.data.get("error") == "blocked_verification_command"

    def test_env_pipe_still_returns_blocked_verification_command(self, tmp_path):
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        result = run_shell_command(ctx, {"command": "env | head -5"})

        assert result.success is False
        assert result.data.get("error") == "blocked_verification_command"


# ---------------------------------------------------------------------------
# 5. Malformed command - shlex.split raises; guard must fall through
# ---------------------------------------------------------------------------

class TestMalformedCommand:
    def test_unterminated_string_falls_through(self, tmp_path):
        """shlex.split raises ValueError on unterminated quotes; must not crash."""
        from rocky.tools.shell import run_shell_command

        ctx = _make_ctx(tmp_path)
        # Should not raise; existing error handling takes over
        result = run_shell_command(ctx, {"command": "'unterminated string"})

        # Whatever result comes back, it must not be the tool_name_in_shell error
        # (the guard must not fire on a malformed command)
        assert result.data.get("error") != "tool_name_in_shell"


# ---------------------------------------------------------------------------
# 6. ALL_TOOL_NAMES sanity
# ---------------------------------------------------------------------------

class TestAllToolNamesSanity:
    def test_all_tool_names_is_frozenset(self):
        from rocky.tools.registry import ALL_TOOL_NAMES
        assert isinstance(ALL_TOOL_NAMES, frozenset)

    def test_all_tool_names_contains_expected_tools(self):
        from rocky.tools.registry import ALL_TOOL_NAMES
        for expected in ("search_web", "run_shell_command", "read_file"):
            assert expected in ALL_TOOL_NAMES, "{!r} missing from ALL_TOOL_NAMES".format(expected)

    def test_all_tool_names_not_empty(self):
        from rocky.tools.registry import ALL_TOOL_NAMES
        assert len(ALL_TOOL_NAMES) >= 3
