"""Scope gate for the defence MCP server.

Reads ``DEFENSE_ALLOWED_TARGETS`` and ``DEFENSE_DENIED_TARGETS`` — the same
env keys blukit's launcher writes into ``.mcp.json``. Deny is checked first,
so a target can be inside the allowed scope and still be refused.

This module is a membership test, not a parser. Blukit normalises what it
writes before the server ever sees it; here we accept the shape blukit
produces (CIDR, host-IP, hostname) and refuse everything else loudly.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Iterable

__all__ = ["Scope", "ScopeDenied", "ALLOW_ENV", "DENY_ENV", "load", "check"]

ALLOW_ENV = "DEFENSE_ALLOWED_TARGETS"
DENY_ENV = "DEFENSE_DENIED_TARGETS"


class ScopeDenied(Exception):
    """Raised when a target is not permitted by the configured scope."""


@dataclass(frozen=True)
class Scope:
    allow_nets: tuple = ()
    allow_hosts: frozenset = frozenset()
    deny_nets: tuple = ()
    deny_hosts: frozenset = frozenset()

    @property
    def is_empty(self) -> bool:
        return not (self.allow_nets or self.allow_hosts)


def _split(raw: str) -> list[str]:
    return [token.strip() for token in raw.split(",") if token.strip()]


def _parse(entries: Iterable[str]) -> tuple[tuple, frozenset]:
    nets: list = []
    hosts: set[str] = set()
    for entry in entries:
        try:
            nets.append(ipaddress.ip_network(entry, strict=True))
            continue
        except ValueError:
            pass
        try:
            addr = ipaddress.ip_address(entry)
            nets.append(ipaddress.ip_network(f"{addr}/{addr.max_prefixlen}", strict=True))
            continue
        except ValueError:
            pass
        hosts.add(entry.lower().rstrip("."))
    return tuple(nets), frozenset(hosts)


def load(env: dict | None = None) -> Scope:
    env = os.environ if env is None else env
    allow_nets, allow_hosts = _parse(_split(env.get(ALLOW_ENV, "")))
    deny_nets, deny_hosts = _parse(_split(env.get(DENY_ENV, "")))
    return Scope(allow_nets, allow_hosts, deny_nets, deny_hosts)


def _match(target: str, nets, hosts) -> bool:
    try:
        addr = ipaddress.ip_address(target)
    except ValueError:
        return target.lower().rstrip(".") in hosts
    return any(addr in net for net in nets)


def check(target: str, scope: Scope) -> None:
    if not isinstance(target, str) or not target:
        raise ScopeDenied("target is required and must be a non-empty string")
    if scope.is_empty:
        raise ScopeDenied("no DEFENSE_ALLOWED_TARGETS configured; refuse-closed")
    if _match(target, scope.deny_nets, scope.deny_hosts):
        raise ScopeDenied(f"{target!r} is in DEFENSE_DENIED_TARGETS")
    if not _match(target, scope.allow_nets, scope.allow_hosts):
        raise ScopeDenied(f"{target!r} is not in DEFENSE_ALLOWED_TARGETS")
