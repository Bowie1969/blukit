"""May this response action be taken against this subject?

blukit's scope gate decides what the launched agent may *act on*. A response --
blocking a host, cutting off an attack in flight -- is such an act, so it is
governed here rather than left to whichever tool server the operator happens to
supply. Without this, the only thing standing between "observe" and "act" would
be the ``env`` block of ``.mcp.json``, which is a file the operator edits by hand
and which the tool server reads directly.

This module **decides and explains; it never executes.** Enforcement belongs to
the operator, behind the ``DEFENSE_RESPONSE_CMD`` seam, because the launched
agent runs unprivileged on purpose: the analyst that can act on the network is
the analyst that can be talked into acting on the wrong thing.

Everything here fails closed. A setting that is absent, empty, or anything other
than the single value that means "on" denies the action and says why. The
refusal reasons are written to be read by the operator mid-incident, so they name
the setting rather than the concept.
"""

from __future__ import annotations

import ipaddress
import os

__all__ = [
    "ALLOW_RESPONSE_ENV_KEY",
    "RESPONSE_CMD_ENV_KEY",
    "TOKEN_ENV_KEY",
    "ACTIONS",
    "Decision",
    "enabled",
    "adapter_configured",
    "allowed",
]

#: The switch. Anything other than exactly "1" -- including "true", "yes" and ""
#: -- is off, because a responder that guesses what the operator meant is a
#: responder that acts when nobody said to.
ALLOW_RESPONSE_ENV_KEY = "DEFENSE_ALLOW_RESPONSE"

#: The enforcement seam. blukit never performs an action itself; when this names
#: a command, that command is what an enforcer would run.
RESPONSE_CMD_ENV_KEY = "DEFENSE_RESPONSE_CMD"

#: Shared secret proving a request came from a session blukit launched, so a
#: stale request from an old run cannot be replayed into a new one.
TOKEN_ENV_KEY = "DEFENSE_TOKEN"

#: The actions this tier implements. A fixed list, never free text: a response
#: tier that accepts arbitrary commands is a remote shell with extra steps.
ACTIONS = frozenset({"block-host"})

SCOPE_ENV_KEY = "DEFENSE_ALLOWED_TARGETS"
DENY_ENV_KEY = "DEFENSE_DENIED_TARGETS"


class Decision:
    """The verdict, and the sentence explaining it.

    ``reason`` is always set -- on a refusal so the operator knows which setting
    to change, and on an allow so the audit record says what permitted it rather
    than only that something did.
    """

    __slots__ = ("allowed", "reason")

    def __init__(self, allowed: bool, reason: str) -> None:
        self.allowed = bool(allowed)
        self.reason = reason

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:
        verdict = "allow" if self.allowed else "deny"
        return f"Decision({verdict}, {self.reason!r})"


def _env(env) -> dict:
    return os.environ if env is None else env


def enabled(env=None) -> bool:
    """True only when the operator turned the response tier on."""
    return _env(env).get(ALLOW_RESPONSE_ENV_KEY, "").strip() == "1"


def adapter_configured(env=None) -> bool:
    """True when an enforcement command is named.

    A tier that is enabled with no adapter can decide but not act, and the
    operator should hear that from ``blukit doctor`` rather than discover it
    mid-incident.
    """
    return bool(_env(env).get(RESPONSE_CMD_ENV_KEY, "").strip())


def _parse_targets(value: str | None) -> tuple[list, list[str]]:
    """Split a blukit scope string into (networks, hostnames).

    blukit normalises before it writes, so this only needs the shapes it writes:
    IPs, CIDRs and hostnames, comma-separated. Hostnames are compared by name --
    resolving would let a resolver widen the scope, which is the failure mode the
    whole gate exists to prevent.
    """
    nets: list = []
    names: list[str] = []
    for token in (value or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            names.append(token.lower().rstrip("."))
    return nets, names


def _subject(token: str):
    token = token.strip()
    try:
        return ipaddress.ip_network(token, strict=False)
    except ValueError:
        return token.lower().rstrip(".")


def _covered(subject, nets, names) -> bool:
    if isinstance(subject, str):
        return subject in names
    return any(
        subject.version == net.version and subject.subnet_of(net) for net in nets
    )


def _touches(subject, nets, names) -> bool:
    """Overlap rather than containment: a denied range denies what it covers."""
    if isinstance(subject, str):
        return subject in names
    return any(
        subject.version == net.version and subject.overlaps(net) for net in nets
    )


def allowed(action: str, subject: str = "", env=None) -> Decision:
    """Decide whether ``action`` against ``subject`` is permitted.

    The order matters and is deliberate: the cheap, unconditional refusals come
    first, so a tier that is switched off cannot be talked into a decision by a
    malformed scope, and the deny list is consulted before the allow list, so a
    deny always wins.
    """
    env = _env(env)

    if not enabled(env):
        return Decision(
            False,
            f"the response tier is off: set {ALLOW_RESPONSE_ENV_KEY}=1 to enable it",
        )

    if action not in ACTIONS:
        known = ", ".join(sorted(ACTIONS))
        return Decision(False, f"unknown response action {action!r}; implemented: {known}")

    subject = (subject or "").strip()
    if not subject:
        return Decision(False, "no subject given: a response must name what it acts on")

    raw = env.get(SCOPE_ENV_KEY, "")
    if not raw.strip():
        return Decision(
            False,
            f"{SCOPE_ENV_KEY} is unset: failing closed, nothing is in scope "
            "(launch through blukit with -s <target>)",
        )
    allow_nets, allow_names = _parse_targets(raw)
    if not allow_nets and not allow_names:
        return Decision(False, f"{SCOPE_ENV_KEY}={raw!r} names no target: failing closed")

    deny_nets, deny_names = _parse_targets(env.get(DENY_ENV_KEY, ""))
    parsed = _subject(subject)

    if _touches(parsed, deny_nets, deny_names):
        return Decision(False, f"{subject} is in {DENY_ENV_KEY}: refused")
    if not _covered(parsed, allow_nets, allow_names):
        return Decision(
            False, f"{subject} is outside {SCOPE_ENV_KEY}={raw}: refused"
        )

    if not adapter_configured(env):
        return Decision(
            False,
            f"{subject} is in scope, but no {RESPONSE_CMD_ENV_KEY} is configured: "
            "nothing can carry the action out, so this refuses rather than let a "
            "caller report a block that would not happen",
        )

    return Decision(True, f"{subject} is inside {SCOPE_ENV_KEY}={raw}")
