"""Tool-handler tests. The mock backend captures argv and returns canned bytes."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from defense import scope as sc  # noqa: E402
from defense import tools as tools_mod  # noqa: E402
from defense.backends import MockBackend  # noqa: E402
from defense.engagement import Engagement  # noqa: E402


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = pathlib.Path(self._tmp.name)
        self.scope = sc.load({"DEFENSE_ALLOWED_TARGETS": "10.50.0.0/24,bwl-portal"})
        self.backend = MockBackend()
        self.engagement = Engagement(self.run_dir)
        for leak in ("DEFENSE_WIN_HOSTS", "DEFENSE_ALLOW_RESPONSE"):
            os.environ.pop(leak, None)

    def _call(self, name, args):
        return tools_mod.dispatch(name, args, self.scope, self.backend, self.engagement)

    # --- scope enforcement --------------------------------------------------

    def test_out_of_scope_host_refused(self):
        reply = self._call("list_connections", {"host": "10.99.0.10"})
        self.assertTrue(reply["isError"])
        self.assertIn("scope denied", reply["content"][0]["text"])

    def test_denied_host_refused_even_in_allow(self):
        scope = sc.load({
            "DEFENSE_ALLOWED_TARGETS": "10.50.0.0/24",
            "DEFENSE_DENIED_TARGETS": "10.50.0.10",
        })
        reply = tools_mod.dispatch("list_connections", {"host": "10.50.0.10"},
                                   scope, self.backend, self.engagement)
        self.assertTrue(reply["isError"])

    # --- individual tools ---------------------------------------------------

    def test_list_connections_dispatches_ss_on_linux(self):
        self.backend.responses[("10.50.0.10", "ss")] = (0, "LISTEN 0 128 *:443 *:*", "")
        reply = self._call("list_connections", {"host": "10.50.0.10"})
        self.assertFalse(reply["isError"])
        self.assertIn("443", reply["content"][0]["text"])
        self.assertEqual(self.backend.calls[0][1][0], "ss")

    def test_list_connections_dispatches_pwsh_on_windows(self):
        with mock.patch.dict(os.environ, {"DEFENSE_WIN_HOSTS": "bwl-portal"}):
            self.backend.responses[("bwl-portal", "pwsh")] = (0, "LocalAddress ...", "")
            reply = self._call("list_connections", {"host": "bwl-portal"})
            self.assertFalse(reply["isError"])
            self.assertEqual(self.backend.calls[0][1][0], "pwsh")

    def test_list_processes_filters_locally(self):
        self.backend.responses[("10.50.0.10", "ps")] = (
            0,
            "root 1 /sbin/init\nnobody 2 nginx: worker\nroot 3 /bin/bash\n",
            "",
        )
        reply = self._call("list_processes", {"host": "10.50.0.10", "filter": "nginx"})
        self.assertFalse(reply["isError"])
        self.assertIn("nginx", reply["content"][0]["text"])
        self.assertNotIn("init", reply["content"][0]["text"])

    def test_list_processes_bad_regex(self):
        self.backend.responses[("10.50.0.10", "ps")] = (0, "x\n", "")
        reply = self._call("list_processes", {"host": "10.50.0.10", "filter": "("})
        self.assertTrue(reply["isError"])

    def test_examine_process_builds_proc_shell_pipeline(self):
        self.backend.responses[("10.50.0.10", "sh")] = (0, "# cmdline\nnginx\n", "")
        reply = self._call("examine_process", {"host": "10.50.0.10", "pid": 1234})
        self.assertFalse(reply["isError"])
        argv = self.backend.calls[0][1]
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertIn("/proc/1234/cmdline", argv[2])

    def test_examine_process_rejects_bad_pid(self):
        reply = self._call("examine_process", {"host": "10.50.0.10", "pid": -1})
        self.assertTrue(reply["isError"])

    def test_query_logs_passes_pattern_through_journalctl(self):
        self.backend.responses[("10.50.0.10", "journalctl")] = (0, "lines", "")
        reply = self._call("query_logs",
                           {"host": "10.50.0.10", "since": "30 min ago", "pattern": "evil"})
        self.assertFalse(reply["isError"])
        argv = self.backend.calls[0][1]
        self.assertIn("--since", argv)
        self.assertIn("30 min ago", argv)
        self.assertIn("--grep", argv)
        self.assertIn("evil", argv)

    def test_query_logs_rejects_control_chars_in_since(self):
        reply = self._call("query_logs", {"host": "10.50.0.10", "since": "a\nb"})
        self.assertTrue(reply["isError"])

    def test_pcap_slice_rejects_bpf_metacharacters(self):
        reply = self._call("pcap_slice", {"host": "10.50.0.10", "bpf": "port 80; rm -rf /"})
        self.assertTrue(reply["isError"])
        self.assertIn("metacharacter", reply["content"][0]["text"])
        self.assertEqual(self.backend.calls, [])

    def test_pcap_slice_duration_bounded(self):
        reply = self._call("pcap_slice", {"host": "10.50.0.10", "duration_s": 999})
        self.assertTrue(reply["isError"])

    def test_pcap_slice_happy_path(self):
        self.backend.responses[("10.50.0.10", "sudo")] = (0, "", "listening on any")
        reply = self._call("pcap_slice",
                           {"host": "10.50.0.10", "bpf": "tcp port 445", "duration_s": 5})
        self.assertFalse(reply["isError"])
        self.assertIn("pcap captured", reply["content"][0]["text"])

    def test_isolate_host_dry_run_default(self):
        reply = self._call("isolate_host", {"host": "10.50.0.10"})
        self.assertFalse(reply["isError"])
        self.assertIn("DRY-RUN", reply["content"][0]["text"])
        self.assertEqual(self.backend.calls, [])

    def test_isolate_host_remains_dry_without_allow_response(self):
        reply = self._call("isolate_host", {"host": "10.50.0.10", "dry_run": False})
        self.assertFalse(reply["isError"])
        self.assertIn("DRY-RUN", reply["content"][0]["text"])
        self.assertEqual(self.backend.calls, [])

    def test_isolate_host_enforces_when_opted_in(self):
        with mock.patch.dict(os.environ, {"DEFENSE_ALLOW_RESPONSE": "1"}):
            self.backend.responses[("10.50.0.10", "sudo")] = (0, "ok", "")
            reply = self._call("isolate_host",
                               {"host": "10.50.0.10", "mode": "egress", "dry_run": False})
            self.assertFalse(reply["isError"])
            self.assertIn("isolated", reply["content"][0]["text"])
            self.assertEqual(self.backend.calls[0][1][:4], ["sudo", "-n", "iptables", "-I"])

    def test_isolate_host_bad_mode(self):
        reply = self._call("isolate_host", {"host": "10.50.0.10", "mode": "nuclear"})
        self.assertTrue(reply["isError"])

    def test_collect_artifact_writes_hashed_file(self):
        self.backend.responses[("10.50.0.10", "COLLECT:/etc/hostname")] = b"bwl-portal\n"
        reply = self._call("collect_artifact",
                           {"host": "10.50.0.10", "path": "/etc/hostname"})
        self.assertFalse(reply["isError"])
        artifacts = list((self.run_dir / "artifacts").iterdir())
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].read_bytes(), b"bwl-portal\n")
        self.assertIn("sha256=", reply["content"][0]["text"])

    def test_read_auth_events_linux(self):
        self.backend.responses[("10.50.0.10", "journalctl")] = (0, "ssh login", "")
        reply = self._call("read_authentication_events", {"host": "10.50.0.10"})
        self.assertFalse(reply["isError"])
        argv = self.backend.calls[0][1]
        self.assertIn("-u", argv)
        self.assertIn("ssh.service", argv)

    def test_engagement_log_appends_both_files(self):
        reply = self._call("engagement_log",
                           {"action": "note", "note": "started phase 2"})
        self.assertFalse(reply["isError"])
        md = (self.run_dir / "log.md").read_text()
        self.assertIn("note", md)
        self.assertIn("started phase 2", md)
        events = (self.run_dir / "events.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(events), 1)

    def test_unknown_tool_name(self):
        reply = self._call("nope", {})
        self.assertTrue(reply["isError"])
        self.assertIn("unknown tool", reply["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
