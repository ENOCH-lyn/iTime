"""Offline regression tests for the project's credential isolation boundary."""

import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import github_auth as auth


class ProjectAuthTests(unittest.TestCase):
    def test_cli_environment_cannot_reuse_global_auth_or_git_injection(self):
        existing = {
            "GH_TOKEN": "global-test-token",
            "GITHUB_TOKEN": "another-global-test-token",
            "GH_ENTERPRISE_TOKEN": "enterprise-test-token",
            "GH_DEBUG": "api",
            "GIT_TRACE_CURL": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "osxkeychain",
            "GIT_CONFIG_PARAMETERS": "unexpected",
            "PATH": "/test/bin",
        }
        with patch.dict(os.environ, existing, clear=True):
            env = auth.cli_environment(Path("/project/.git/github"), "project-test-token")
        self.assertEqual(env["GH_TOKEN"], "project-test-token")
        self.assertEqual(env["GH_CONFIG_DIR"], "/project/.git/github")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/project/.git/github/gitconfig")
        self.assertEqual(env["GIT_CONFIG_SYSTEM"], os.devnull)
        self.assertFalse(set(existing) - {"GH_TOKEN", "PATH"} & set(env))

    def test_credentials_are_atomic_and_owner_only(self):
        with tempfile.TemporaryDirectory(dir=auth.ROOT / ".git") as folder:
            directory = Path(folder)
            data = {"login": "test-user", "access_token": "fake-local-token"}
            auth.save_credentials(directory, data)
            path = directory / "credentials.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(auth.read_credentials(directory), data)
            self.assertEqual(list(directory.iterdir()), [path])

    def test_git_helper_only_serves_the_configured_repository(self):
        credentials = {"login": "test-user", "access_token": "fake-local-token"}
        config = subprocess.CompletedProcess([], 0, stdout="test-user/iTime\n")
        for host, path, expected in [
            ("github.com", "test-user/iTime.git", True),
            ("github.com", "test-user/iTime", True),
            ("github.com", "test-user/other.git", False),
            ("github.com", "other-user/iTime.git", False),
            ("github.com.evil.invalid", "test-user/iTime.git", False),
            ("github.com", "", False),
        ]:
            with self.subTest(host=host, path=path):
                output = io.StringIO()
                source = io.StringIO(f"protocol=https\nhost={host}\npath={path}\n\n")
                with patch.object(auth.subprocess, "run", return_value=config), \
                     patch.object(auth, "read_credentials", return_value=credentials):
                    auth.credential_get(Path("/unused"), "get", source, output)
                self.assertEqual("fake-local-token" in output.getvalue(), expected)

    def test_store_and_erase_never_delegate_or_read_credentials(self):
        for action in ["store", "erase"]:
            with patch.object(auth.subprocess, "run") as run, \
                 patch.object(auth, "read_credentials") as read:
                output = io.StringIO()
                auth.credential_get(Path("/unused"), action, io.StringIO(""), output)
                run.assert_not_called()
                read.assert_not_called()
                self.assertEqual(output.getvalue(), "")

    def test_login_uses_device_flow_without_running_gh_or_keychain(self):
        responses = [
            {"device_code": "fake-device", "user_code": "TEST-CODE", "expires_in": 900, "interval": 5},
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {"access_token": "fake-local-token", "scope": "repo"},
            {"login": "test-user"},
        ]
        with tempfile.TemporaryDirectory(dir=auth.ROOT / ".git") as folder, \
             patch.object(auth, "request_json", side_effect=responses), \
             patch.object(auth.time, "sleep"), \
             patch.object(auth.subprocess, "run") as run, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            auth.device_login(Path(folder))
            run.assert_not_called()
            self.assertNotIn("fake-local-token", output.getvalue())
            saved = json.loads((Path(folder) / "credentials.json").read_text())
            self.assertEqual(saved["login"], "test-user")

    def test_auth_mutation_commands_are_blocked(self):
        for command in ["setup-git", "refresh", "switch", "logout", "token"]:
            with patch.object(auth, "auth_directory", return_value=Path("/unused")), \
                 patch.object(auth.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    auth.main(["auth", command])
                run.assert_not_called()

    def test_missing_project_token_cannot_fall_back_to_global_login(self):
        with tempfile.TemporaryDirectory(dir=auth.ROOT / ".git") as folder, \
             patch.object(auth, "auth_directory", return_value=Path(folder)), \
             patch.object(auth.subprocess, "run") as run, \
             patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(auth.main(["api", "user"]), 1)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
