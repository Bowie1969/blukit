"""Tests for ``blukit init`` and ``blukit run --init`` — scaffolding a wired
``.mcp.json`` so an engagement can start from nothing but the protected target.

The scaffold is exercised through the CLI in-process (``call_main``); the
``run --init`` launch path reaches ``os.execvpe`` and so runs in a subprocess
against the stub binary, exactly as the other launch tests do.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _helpers import SCOPE_ENV_KEY, BlukitTestCase  # noqa: E402

from blukit.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE  # noqa: E402


class InitTestCase(BlukitTestCase):
    def defense_env(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["mcpServers"]["defense"]["env"]

    def backups(self):
        return sorted(p.name for p in self.tmp.glob(".mcp.json*.bak"))


class TestInitScaffold(InitTestCase):
    def test_init_writes_a_wired_defense_server_from_the_target_alone(self):
        code, out, err = self.call_main(["init", "-s", "10.66.66.10/32", "-y"])

        self.assertEqual(code, EXIT_OK, err)
        path = self.tmp / ".mcp.json"
        self.assertTrue(path.is_file())
        server = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["defense"]
        self.assertEqual(server["command"], "python3")
        self.assertEqual(server["args"], ["defense_server.py"])
        env = server["env"]
        self.assertEqual(env[SCOPE_ENV_KEY], "10.66.66.10/32")
        # Every optional capability is scaffolded off, and the deny list is empty.
        self.assertEqual(env["DEFENSE_DENIED_TARGETS"], "")
        self.assertEqual(env["DEFENSE_ALLOW_RESPONSE"], "0")
        self.assertEqual(env["DEFENSE_ALLOW_INTERPRETER"], "0")
        self.assertEqual(env["DEFENSE_ALLOW_ANY_CMD"], "0")

    def test_init_accepts_a_webapp_url_and_records_its_host(self):
        code, _, err = self.call_main(["init", "-s", "https://app.lan:8443/api/v1", "-y"])

        self.assertEqual(code, EXIT_OK, err)
        env = self.defense_env(self.tmp / ".mcp.json")
        self.assertEqual(env[SCOPE_ENV_KEY], "app.lan")

    def test_init_records_a_deny_list_and_the_run_dir(self):
        code, _, err = self.call_main(
            [
                "init",
                "-s",
                "10.66.66.0/24",
                "--deny",
                "10.66.66.1/32",
                "--run-dir",
                "/runs/latest",
                "-y",
            ]
        )

        self.assertEqual(code, EXIT_OK, err)
        env = self.defense_env(self.tmp / ".mcp.json")
        self.assertEqual(env["DEFENSE_DENIED_TARGETS"], "10.66.66.1/32")
        self.assertEqual(env["DEFENSE_RUN_DIR"], "/runs/latest")

    def test_init_uses_an_explicit_defense_server_path(self):
        code, _, err = self.call_main(
            ["init", "-s", "solo", "--defense-server", "/opt/defense/defense_server.py", "-y"]
        )

        self.assertEqual(code, EXIT_OK, err)
        server = json.loads((self.tmp / ".mcp.json").read_text(encoding="utf-8"))[
            "mcpServers"
        ]["defense"]
        self.assertEqual(server["args"], ["/opt/defense/defense_server.py"])

    def test_init_reads_the_defense_server_from_the_environment(self):
        with mock.patch.dict(os.environ, {"BLUKIT_DEFENSE_SERVER": "/srv/defense.py"}):
            code, _, err = self.call_main(["init", "-s", "solo", "-y"])

        self.assertEqual(code, EXIT_OK, err)
        server = json.loads((self.tmp / ".mcp.json").read_text(encoding="utf-8"))[
            "mcpServers"
        ]["defense"]
        self.assertEqual(server["args"], ["/srv/defense.py"])

    def test_init_refuses_to_overwrite_an_existing_file(self):
        path = self.write_mcp_json({"mcpServers": {"keep": {"command": "x"}}})

        code, _, err = self.call_main(["init", "-s", "10.0.0.0/24", "-y"])

        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("already exists", err)
        # The original file is left exactly as it was.
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"mcpServers": {"keep": {"command": "x"}}},
        )
        self.assertEqual(self.backups(), [])

    def test_init_force_overwrites_and_backs_up_the_old_file(self):
        path = self.write_mcp_json({"mcpServers": {"keep": {"command": "x"}}})

        code, _, err = self.call_main(["init", "-s", "10.0.0.0/24", "-y", "--force"])

        self.assertEqual(code, EXIT_OK, err)
        self.assertEqual(self.defense_env(path)[SCOPE_ENV_KEY], "10.0.0.0/24")
        self.assertEqual(len(self.backups()), 1)

    def test_init_dry_run_writes_nothing(self):
        code, out, err = self.call_main(["init", "-s", "10.0.0.0/24", "-y", "-n"])

        self.assertEqual(code, EXIT_OK, err)
        self.assertIn("would write", out)
        self.assertFalse((self.tmp / ".mcp.json").exists())

    def test_init_rejects_a_bad_scope_and_writes_nothing(self):
        code, _, err = self.call_main(["init", "-s", "10.0.0.0/24;rm -rf /", "-y"])

        self.assertEqual(code, EXIT_USAGE)
        self.assertFalse((self.tmp / ".mcp.json").exists())


class TestRunInit(InitTestCase):
    def test_run_init_scaffolds_then_launches_from_the_target_alone(self):
        # No .mcp.json exists; --init creates one and the launch enforces it.
        proc = self.run_cli(["run", "--init", "-s", "10.66.66.10/32", "-y", "--", "--model", "opus"])

        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertIn("scope:10.66.66.10/32", proc.stdout)
        self.assertIn("identity:blukit", proc.stdout)
        path = self.tmp / ".mcp.json"
        self.assertTrue(path.is_file())
        self.assertEqual(self.defense_env(path)[SCOPE_ENV_KEY], "10.66.66.10/32")

    def test_run_init_reduces_a_url_target_to_its_host(self):
        proc = self.run_cli(
            ["run", "--init", "-s", "https://app.lan:8443/api", "-y", "--", "--model", "opus"]
        )

        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertIn("scope:app.lan", proc.stdout)

    def test_run_init_does_not_clobber_an_existing_config(self):
        # A file already present is used as-is; --init must not overwrite it.
        self.write_mcp_json(self.defense_fixture("127.0.0.1/32"))

        proc = self.run_cli(["run", "--init", "-s", "10.0.0.0/24", "-y", "--", "--model", "opus"])

        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        # The scope is recorded into the existing defense server, which still
        # carries its unrelated content from the fixture.
        data = json.loads((self.tmp / ".mcp.json").read_text(encoding="utf-8"))
        self.assertIn("mission", data["mcpServers"])
        self.assertEqual(
            data["mcpServers"]["defense"]["env"][SCOPE_ENV_KEY], "10.0.0.0/24"
        )

    def test_run_init_dry_run_previews_without_writing(self):
        code, out, err = self.call_main(
            ["run", "--init", "-s", "10.0.0.0/24", "-y", "-n", "--", "--model", "opus"]
        )

        self.assertEqual(code, EXIT_OK, err)
        self.assertIn("would scaffold", out)
        self.assertIn("would launch", out)
        self.assertFalse((self.tmp / ".mcp.json").exists())


if __name__ == "__main__":
    unittest.main()
