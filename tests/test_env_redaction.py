Status: DONE
"""Tests for environment-variable redaction (O15 / PRE-2).

Coverage:
1. redact_env_output unit tests
2. Shell tool integration — stdout redacted
3. Shell tool env-command blocklist
4. Trace-persist redaction path (utility level)
"""

import json
from pathlib import Path

import pytest

from rocky.util.redaction import redact_env_output, BLOCKED_VERIFICATION_COMMANDS


# ---------------------------------------------------------------------------
# 1. Unit tests for redact_env_output
# ---------------------------------------------------------------------------

class TestRedactEnvOutput:
    def test_aws_secret_key_is_redacted(self):
        text = "AWS_SECRET_ACCESS_KEY=realvalue\nFOO=bar"
        result = redact_env_output(text)
        assert "AWS_SECRET_ACCESS_KEY=<redacted>" in result
        assert "FOO=bar" in result

    def test_database_password_is_redacted(self):
        result = redact_env_output("DATABASE_PASSWORD=xyz")
        assert "DATABASE_PASSWORD=<redacted>" in result
        assert "xyz" not in result

    def test_github_token_is_redacted(self):
        result = redact_env_output("GITHUB_TOKEN=ghp_abc123")
        assert "GITHUB_TOKEN=<redacted>" in result
        assert "ghp_abc123" not in result

    def test_custom_api_key_is_redacted(self):
        result = redact_env_output("MY_CUSTOM_API_KEY=supersecret")
        assert "MY_CUSTOM_API_KEY=<redacted>" in result
        assert "supersecret" not in result

    def test_ssh_auth_sock_is_redacted(self):
        # AUTH appears in the key name — redacted (safe-by-default)
        result = redact_env_output("SSH_AUTH_SOCK=/tmp/ssh_agent.socket")
        assert "SSH_AUTH_SOCK=<redacted>" in result

    def test_non_sensitive_var_passes_through(self):
        result = redact_env_output("BUCKET_SIZE_MAX=4096")
        assert "BUCKET_SIZE_MAX=4096" in result

    def test_path_is_not_redacted(self):
        result = redact_env_output("PATH=/usr/local/bin:/usr/bin:/bin")
        assert "PATH=/usr/local/bin:/usr/bin:/bin" in result

    def test_mixed_multiline(self):
        text = "HOME=/home/user\nSTRIPE_SECRET_KEY=sk_live_abc\nSHELL=/bin/zsh"
        result = redact_env_output(text)
        assert "HOME=/home/user" in result
        assert "STRIPE_SECRET_KEY=<redacted>" in result
        assert "sk_live_abc" not in result
        assert "SHELL=/bin/zsh" in result

    def test_idempotent(self):
        text = "API_KEY=abc123\nFOO=bar"
        once = redact_env_output(text)
        twice = redact_env_output(once)
        assert once == twice

    def test_non_matching_prose_unchanged(self):
        prose = "the key to success lies in hard work"
        assert redact_env_output(prose) == prose

    def test_no_equals_sign_unchanged(self):
        text = "GITHUB_TOKEN"
        assert redact_env_output(text) == text


# ---------------------------------------------------------------------------
# 2. Shell tool integration -- redaction applied to subprocess output
# ---------------------------------------------------------------------------

class TestShellToolRedaction:
    def test_sensitive_stdout_is_redacted(self, tmp_path: Path):
        from rocky.app import RockyRuntime
        from rocky.tools.shell import run_shell_command

        runtime = RockyRuntime.load_from(tmp_path)
        runtime.permissions.config.mode = "bypass"

        result = run_shell_command(
            runtime.tool_registry.context,
            {"command": "echo 'STRIPE_API_KEY=sk_test_fakevalue_abc'"},
        )

        assert result.success is True
        assert "STRIPE_API_KEY=<redacted>" in result.data["stdout"]
        assert "sk_test_fakevalue_abc" not in result.data["stdout"]

    def test_non_sensitive_output_preserved(self, tmp_path: Path):
        from rocky.app import RockyRuntime
        from rocky.tools.shell import run_shell_command

        runtime = RockyRuntime.load_from(tmp_path)
        runtime.permissions.config.mode = "bypass"

        result = run_shell_command(
            runtime.tool_registry.context,
            {"command": "echo 'HELLO_WORLD=visible'"},
        )

        assert result.success is True
        assert "HELLO_WORLD=visible" in result.data["stdout"]


# ---------------------------------------------------------------------------
# 3. env-command blocklist
# ---------------------------------------------------------------------------

class TestEnvCommandBlocklist:
    def test_env_command_is_blocked(self, tmp_path: Path):
        from rocky.app import RockyRuntime
        from rocky.tools.shell import run_shell_command

        runtime = RockyRuntime.load_from(tmp_path)
        runtime.permissions.config.mode = "bypass"

        result = run_shell_command(
            runtime.tool_registry.context,
            {"command": "env"},
        )

        assert result.success is False
        assert result.data.get("error") == "blocked_verification_command"
        # Real env vars must NOT appear in the output data
        home_val = Path.home()
        assert str(home_val) not in str(result.data.get("message", ""))

    def test_env_command_with_pipe_is_blocked(self, tmp_path: Path):
        from rocky.app import RockyRuntime
        from rocky.tools.shell import run_shell_command

        runtime = RockyRuntime.load_from(tmp_path)
        runtime.permissions.config.mode = "bypass"

        result = run_shell_command(
            runtime.tool_registry.context,
            {"command": "env | head -5"},
        )

        assert result.success is False
        assert result.data.get("error") == "blocked_verification_command"

    def test_printenv_is_not_blocked(self, tmp_path: Path):
        from rocky.app import RockyRuntime
        from rocky.tools.shell import run_shell_command

        runtime = RockyRuntime.load_from(tmp_path)
        runtime.permissions.config.mode = "bypass"

        result = run_shell_command(
            runtime.tool_registry.context,
            {"command": "printenv PATH"},
        )

        # printenv is allowed (argv[0] == "printenv", not in blocklist)
        assert result.data.get("error") != "blocked_verification_command"

    def test_blocked_commands_constant_contains_env(self):
        assert "env" in BLOCKED_VERIFICATION_COMMANDS


# ---------------------------------------------------------------------------
# 4. Trace-persist redaction (utility level)
# ---------------------------------------------------------------------------

class TestTracePersistRedaction:
    def test_redact_applied_to_json_payload_string(self):
        """Simulate what agent.py does: serialize a payload to JSON then apply redact."""
        payload = {
            "tool_events": [
                {
                    "type": "tool_result",
                    "stdout": "STRIPE_API_KEY=sk_test_x\nSOME_VAR=innocent",
                }
            ]
        }
        serialized = json.dumps(payload)
        redacted = redact_env_output(serialized)

        assert "sk_test_x" not in redacted
        assert "STRIPE_API_KEY=<redacted>" in redacted
        assert "SOME_VAR=innocent" in redacted

    def test_redact_env_output_used_at_write_site(self):
        """Confirm the import exists in agent.py -- structural guard."""
        import rocky.core.agent as agent_module
        import inspect
        source = inspect.getsource(agent_module)
        assert "redact_env_output" in source
        assert "write_text" in source
