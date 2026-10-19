"""MCP stdio-server handshake and dispatch tests.

Drives the server with ``io.StringIO`` for stdin/stdout, so no subprocess
and no real backend. The initialize/tools-list/tools-call handshake is
checked end to end, together with the error replies for parse errors,
unknown methods, and out-of-scope tool calls.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from defense import server  # noqa: E402


class ServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = {
            "DEFENSE_ALLOWED_TARGETS": "10.0.0.0/24",
            "DEFENSE_BACKEND": "mock",
            "DEFENSE_RUN_DIR": self._tmp.name,
        }

    def _run(self, messages):
        stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
        stdout = io.StringIO()
        rc = server.serve(stdin=stdin, stdout=stdout, env=self.env)
        self.assertEqual(rc, 0)
        return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]

    def test_initialize_echoes_protocol_and_names_server(self):
        out = self._run([{
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        }])
        self.assertEqual(out[0]["id"], 1)
        self.assertEqual(out[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "defense")
        self.assertIn("tools", out[0]["result"]["capabilities"])

    def test_initialized_notification_produces_no_reply(self):
        out = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ])
        # Only initialize's reply; notification is silent.
        self.assertEqual(len(out), 1)

    def test_tools_list_covers_the_minimum_viable_set(self):
        out = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        names = {t["name"] for t in out[-1]["result"]["tools"]}
        self.assertEqual(names, {
            "query_logs",
            "list_connections",
            "list_processes",
            "examine_process",
            "pcap_slice",
            "isolate_host",
            "collect_artifact",
            "read_authentication_events",
            "engagement_log",
        })
        for tool in out[-1]["result"]["tools"]:
            self.assertIn("inputSchema", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_tools_call_dispatches_to_handler(self):
        out = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "engagement_log", "arguments": {"action": "note", "note": "hi"}}},
        ])
        self.assertFalse(out[-1]["result"]["isError"])
        self.assertTrue((pathlib.Path(self._tmp.name) / "log.md").exists())

    def test_tools_call_scope_denied(self):
        out = self._run([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "list_processes", "arguments": {"host": "10.99.0.10"}}},
        ])
        self.assertTrue(out[-1]["result"]["isError"])
        self.assertIn("scope denied", out[-1]["result"]["content"][0]["text"])

    def test_parse_error_on_bad_json(self):
        stdin = io.StringIO("{not json}\n")
        stdout = io.StringIO()
        server.serve(stdin=stdin, stdout=stdout, env=self.env)
        reply = json.loads(stdout.getvalue().strip())
        self.assertEqual(reply["error"]["code"], -32700)
        self.assertIsNone(reply["id"])

    def test_unknown_method_returns_method_not_found(self):
        out = self._run([{"jsonrpc": "2.0", "id": 7, "method": "frobnicate"}])
        self.assertEqual(out[0]["id"], 7)
        self.assertEqual(out[0]["error"]["code"], -32601)

    def test_ping_is_pong(self):
        out = self._run([{"jsonrpc": "2.0", "id": 9, "method": "ping"}])
        self.assertEqual(out[0]["id"], 9)
        self.assertEqual(out[0]["result"], {})


if __name__ == "__main__":
    unittest.main()
