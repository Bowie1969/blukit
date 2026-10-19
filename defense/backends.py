"""Subprocess and mock backends for the defence MCP tools.

A backend turns a logical operation ("run this argv on this host") into
captured output. The real backend wraps ``ssh`` (``argv``-only, never a
shell string); the mock backend returns canned bytes from a dict so tests
never touch the network.

The real backend assumes OpenSSH on both sides. Linux hosts run the argv
as-given through the user's remote login shell; Windows hosts (Server
2022 + OpenSSH Server) are issued ``pwsh -NoProfile -Command ...``
elsewhere in the tool layer. Key-based auth is required
(``BatchMode=yes``); the server never prompts for a password.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path
from typing import Protocol

__all__ = ["Backend", "SubprocessBackend", "MockBackend", "select"]


class Backend(Protocol):
    def run(self, host: str, argv: list[str], *, timeout: int = 30) -> tuple[int, str, str]: ...
    def collect(self, host: str, path: str, dest: Path) -> dict: ...


class SubprocessBackend:
    """Runs remote commands over SSH. argv-only, no shell=True anywhere."""

    def __init__(self, ssh_bin: str = "ssh", env: dict | None = None):
        env = os.environ if env is None else env
        self.ssh_bin = ssh_bin
        self.ssh_user = env.get("DEFENSE_SSH_USER", "root")

    def _ssh(self, host: str) -> list[str]:
        return [
            self.ssh_bin,
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            f"{self.ssh_user}@{host}",
        ]

    def run(self, host, argv, *, timeout=30):
        remote = " ".join(shlex.quote(a) for a in argv)
        proc = subprocess.run(
            [*self._ssh(host), remote],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def collect(self, host, path, dest):
        with open(dest, "wb") as fh:
            proc = subprocess.run(
                [*self._ssh(host), "cat", shlex.quote(path)],
                stdout=fh, stderr=subprocess.PIPE, timeout=60,
            )
        data = dest.read_bytes()
        return {
            "ok": proc.returncode == 0,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "stderr": proc.stderr.decode(errors="replace") if proc.stderr else "",
        }


class MockBackend:
    """In-process backend. Tests seed ``.responses`` and inspect ``.calls``."""

    def __init__(self):
        self.responses: dict = {}
        self.calls: list = []

    def run(self, host, argv, *, timeout=30):
        self.calls.append((host, list(argv)))
        key = (host, argv[0] if argv else "")
        rc, out, err = self.responses.get(key, (0, "", ""))
        return rc, out, err

    def collect(self, host, path, dest):
        self.calls.append((host, ["COLLECT", path]))
        payload = self.responses.get((host, f"COLLECT:{path}"), b"<mock-contents>")
        if isinstance(payload, str):
            payload = payload.encode()
        dest.write_bytes(payload)
        return {
            "ok": True,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "stderr": "",
        }


def select(env: dict | None = None) -> Backend:
    env = os.environ if env is None else env
    choice = env.get("DEFENSE_BACKEND", "subprocess").lower()
    if choice == "mock":
        return MockBackend()
    return SubprocessBackend(env=env)
