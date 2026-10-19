"""Scope-gate tests for the defence MCP server.

The gate is a membership test over ``DEFENSE_ALLOWED_TARGETS`` and
``DEFENSE_DENIED_TARGETS``. Deny wins over allow; an empty allow refuses
every target (fail-closed); a bare host-IP is lifted to a host-prefix net;
hostname match is case-insensitive and strips a trailing dot.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from defense import scope as sc  # noqa: E402


class ScopeTests(unittest.TestCase):
    def test_cidr_allows_members_and_refuses_outsiders(self):
        s = sc.load({"DEFENSE_ALLOWED_TARGETS": "10.0.0.0/24"})
        sc.check("10.0.0.5", s)
        with self.assertRaises(sc.ScopeDenied):
            sc.check("10.0.1.5", s)

    def test_hostname_match_is_case_insensitive(self):
        s = sc.load({"DEFENSE_ALLOWED_TARGETS": "app.lan"})
        sc.check("app.lan", s)
        sc.check("APP.LAN", s)
        sc.check("app.lan.", s)
        with self.assertRaises(sc.ScopeDenied):
            sc.check("other.lan", s)

    def test_bare_ip_lifts_to_host_prefix(self):
        s = sc.load({"DEFENSE_ALLOWED_TARGETS": "10.0.0.5"})
        sc.check("10.0.0.5", s)
        with self.assertRaises(sc.ScopeDenied):
            sc.check("10.0.0.6", s)

    def test_deny_beats_allow(self):
        s = sc.load({
            "DEFENSE_ALLOWED_TARGETS": "10.0.0.0/24",
            "DEFENSE_DENIED_TARGETS": "10.0.0.5",
        })
        sc.check("10.0.0.4", s)
        with self.assertRaises(sc.ScopeDenied) as cm:
            sc.check("10.0.0.5", s)
        self.assertIn("DENIED", str(cm.exception))

    def test_empty_allow_refuses_everything(self):
        s = sc.load({"DEFENSE_ALLOWED_TARGETS": ""})
        with self.assertRaises(sc.ScopeDenied):
            sc.check("127.0.0.1", s)

    def test_missing_env_refuses(self):
        s = sc.load({})
        self.assertTrue(s.is_empty)
        with self.assertRaises(sc.ScopeDenied):
            sc.check("anything", s)

    def test_mixed_cidr_and_hostname(self):
        s = sc.load({"DEFENSE_ALLOWED_TARGETS": "10.50.0.0/24,bwl-portal,bwl-dealroom"})
        sc.check("10.50.0.10", s)
        sc.check("bwl-portal", s)
        sc.check("bwl-dealroom", s)
        with self.assertRaises(sc.ScopeDenied):
            sc.check("bwl-paralegal", s)

    def test_non_string_target_refused(self):
        s = sc.load({"DEFENSE_ALLOWED_TARGETS": "10.0.0.0/24"})
        with self.assertRaises(sc.ScopeDenied):
            sc.check("", s)
        with self.assertRaises(sc.ScopeDenied):
            sc.check(None, s)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
