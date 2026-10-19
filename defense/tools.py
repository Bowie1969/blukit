"""The defence MCP tool surface.

Each tool is scope-gated against ``DEFENSE_ALLOWED_TARGETS`` /
``DEFENSE_DENIED_TARGETS`` before any backend call. Return values follow
MCP's ``{content: [{type: text, text: ...}], isError: bool}`` shape so the
client can display them without further wrapping.

Platform split: ``DEFENSE_WIN_HOSTS`` (comma-separated hosts/IPs) names
the Windows targets. Everything else is Linux. The alternative —
autodetecting with a probe — costs an SSH round-trip per call, so the
operator declares it.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass
from typing import Callable

from . import scope as scope_mod
from .backends import Backend
from .engagement import Engagement

__all__ = ["Tool", "all_tools", "dispatch"]


_BPF_UNSAFE = re.compile(r"[;&|`$\"\\<>()]")
_SINCE_UNSAFE = re.compile(r"[\r\n\x00]")


def _is_windows(host: str) -> bool:
    raw = os.environ.get("DEFENSE_WIN_HOSTS", "")
    return host.lower() in {h.strip().lower() for h in raw.split(",") if h.strip()}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable


def _text(payload: str) -> dict:
    return {"content": [{"type": "text", "text": payload}], "isError": False}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def _require_str(args, key, default=None):
    value = args.get(key, default)
    if value is None:
        raise KeyError(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _check_since(since: str) -> str:
    if _SINCE_UNSAFE.search(since):
        raise ValueError("since contains control characters")
    return since


# ---------------------------------------------------------------- tools --


def _tool_query_logs(args, scope, backend, engagement):
    host = _require_str(args, "host")
    since = _check_since(args.get("since", "1 hour ago"))
    pattern = args.get("pattern", "") or ""
    scope_mod.check(host, scope)
    if _is_windows(host):
        argv = ["pwsh", "-NoProfile", "-Command",
                "Get-WinEvent -FilterHashtable @{LogName='Security'; StartTime=((Get-Date).AddHours(-1))} "
                f"| Where-Object Message -match {json.dumps(pattern or '.*')} | Format-List"]
    else:
        argv = ["journalctl", "--no-pager", "--since", since]
        if pattern:
            argv += ["--grep", pattern]
    rc, out, err = backend.run(host, argv, timeout=60)
    engagement.append("query_logs",
                      {"host": host, "since": since, "pattern": pattern, "rc": rc})
    return _text(out or err or f"[rc={rc}] (no output)")


def _tool_list_connections(args, scope, backend, engagement):
    host = _require_str(args, "host")
    scope_mod.check(host, scope)
    argv = (["pwsh", "-NoProfile", "-Command", "Get-NetTCPConnection | Format-Table -Auto"]
            if _is_windows(host) else ["ss", "-tnp"])
    rc, out, err = backend.run(host, argv)
    engagement.append("list_connections", {"host": host, "rc": rc})
    return _text(out or err or f"[rc={rc}]")


def _tool_list_processes(args, scope, backend, engagement):
    host = _require_str(args, "host")
    scope_mod.check(host, scope)
    pat = args.get("filter")
    if _is_windows(host):
        cmd = "Get-Process | Format-Table -Auto"
        if pat:
            cmd = ("Get-Process | Where-Object {{ $_.ProcessName -match "
                   "{} }} | Format-Table -Auto").format(json.dumps(pat))
        argv = ["pwsh", "-NoProfile", "-Command", cmd]
    else:
        argv = ["ps", "auxww"]
    rc, out, err = backend.run(host, argv)
    if pat and not _is_windows(host) and rc == 0:
        try:
            needle = re.compile(pat)
            out = "\n".join(line for line in out.splitlines() if needle.search(line))
        except re.error as exc:
            return _err(f"invalid filter regex: {exc}")
    engagement.append("list_processes", {"host": host, "filter": pat, "rc": rc})
    return _text(out or err or f"[rc={rc}]")


def _tool_examine_process(args, scope, backend, engagement):
    host = _require_str(args, "host")
    pid = int(args["pid"])
    if pid <= 0:
        return _err("pid must be a positive integer")
    scope_mod.check(host, scope)
    if _is_windows(host):
        argv = ["pwsh", "-NoProfile", "-Command",
                f"Get-Process -Id {pid} | Format-List *; "
                f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' "
                f"| Format-List CommandLine, ParentProcessId"]
    else:
        argv = ["sh", "-c",
                f"echo '# cmdline'; tr '\\000' ' ' </proc/{pid}/cmdline 2>/dev/null; echo; "
                f"echo '# status'; cat /proc/{pid}/status 2>/dev/null; "
                f"echo '# environ'; tr '\\000' '\\n' </proc/{pid}/environ 2>/dev/null; "
                f"echo '# maps'; head -20 /proc/{pid}/maps 2>/dev/null; "
                f"echo '# fd'; ls -la /proc/{pid}/fd 2>/dev/null"]
    rc, out, err = backend.run(host, argv)
    engagement.append("examine_process", {"host": host, "pid": pid, "rc": rc})
    return _text(out or err or f"[rc={rc}]")


def _tool_pcap_slice(args, scope, backend, engagement):
    host = _require_str(args, "host")
    bpf = args.get("bpf", "") or ""
    duration = int(args.get("duration_s", 10))
    scope_mod.check(host, scope)
    if duration <= 0 or duration > 300:
        return _err("duration_s must be between 1 and 300")
    if _BPF_UNSAFE.search(bpf):
        return _err("bpf filter contains a shell metacharacter; refusing")
    out_path = f"/tmp/defense-{int(_dt.datetime.now().timestamp())}.pcap"
    base = ["sudo", "-n", "tcpdump", "-i", "any", "-w", out_path,
            "-G", str(duration), "-W", "1"]
    argv = base + ([bpf] if bpf else [])
    rc, out, err = backend.run(host, argv, timeout=duration + 30)
    engagement.append("pcap_slice",
                      {"host": host, "bpf": bpf, "duration_s": duration,
                       "rc": rc, "path": out_path})
    return _text(f"pcap captured to {host}:{out_path}\n[rc={rc}]\n{err or out}")


def _tool_isolate_host(args, scope, backend, engagement):
    host = _require_str(args, "host")
    mode = args.get("mode", "egress")
    dry_run = args.get("dry_run")
    if dry_run is None:
        dry_run = True
    scope_mod.check(host, scope)
    if mode not in ("egress", "full"):
        return _err(f"mode must be 'egress' or 'full', got {mode!r}")
    allow_response = os.environ.get("DEFENSE_ALLOW_RESPONSE", "0") == "1"
    if _is_windows(host):
        if mode == "egress":
            cmd = ("New-NetFirewallRule -DisplayName 'defense-isolate-egress' "
                   "-Direction Outbound -Action Block -RemoteAddress Any")
        else:
            cmd = ("New-NetFirewallRule -DisplayName 'defense-isolate-full-in' "
                   "-Direction Inbound -Action Block; "
                   "New-NetFirewallRule -DisplayName 'defense-isolate-full-out' "
                   "-Direction Outbound -Action Block")
        argv = ["pwsh", "-NoProfile", "-Command", cmd]
    else:
        chain = "OUTPUT" if mode == "egress" else "INPUT"
        argv = ["sudo", "-n", "iptables", "-I", chain, "-j", "DROP"]
    if dry_run or not allow_response:
        preview = " ".join(argv)
        engagement.append("isolate_host",
                          {"host": host, "mode": mode, "dry_run": True,
                           "preview": preview, "allow_response": allow_response})
        reason = "dry_run=True" if dry_run else "DEFENSE_ALLOW_RESPONSE!=1"
        return _text(
            f"DRY-RUN ({reason}): would isolate {host} ({mode})\n"
            f"argv: {preview}\n"
            f"Enable with DEFENSE_ALLOW_RESPONSE=1 and dry_run=false."
        )
    rc, out, err = backend.run(host, argv, timeout=30)
    engagement.append("isolate_host",
                      {"host": host, "mode": mode, "dry_run": False, "rc": rc})
    return _text(f"isolated {host} ({mode})\n[rc={rc}]\n{err or out}")


def _tool_collect_artifact(args, scope, backend, engagement):
    host = _require_str(args, "host")
    path = _require_str(args, "path")
    scope_mod.check(host, scope)
    artifacts = engagement.run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{host}_{path}").strip("_")[:200]
    dest = artifacts / safe_name
    info = backend.collect(host, path, dest)
    engagement.append("collect_artifact",
                      {"host": host, "path": path, "dest": str(dest), **info})
    return _text(f"collected {host}:{path} -> {dest}\n"
                 f"sha256={info['sha256']} bytes={info['bytes']}")


def _tool_read_auth_events(args, scope, backend, engagement):
    host = _require_str(args, "host")
    since = _check_since(args.get("since", "6 hours ago"))
    scope_mod.check(host, scope)
    if _is_windows(host):
        argv = ["pwsh", "-NoProfile", "-Command",
                "Get-WinEvent -LogName Security -MaxEvents 500 | "
                "Where-Object { $_.Id -in 4624,4625,4634,4648,4672,4720 } | "
                "Format-List TimeCreated,Id,Message"]
    else:
        argv = ["journalctl", "--no-pager", "-u", "ssh.service", "-u", "sshd.service",
                "-u", "sudo", "--since", since]
    rc, out, err = backend.run(host, argv, timeout=60)
    engagement.append("read_authentication_events",
                      {"host": host, "since": since, "rc": rc})
    return _text(out or err or f"[rc={rc}]")


def _tool_engagement_log(args, scope, backend, engagement):
    action = _require_str(args, "action")
    note = args.get("note", "") or ""
    engagement.append(action, {"note": note, "source": "operator"})
    return _text(f"logged: {action}: {note}")


# --------------------------------------------------------------- registry --


_TOOLS = [
    Tool("query_logs",
         "Search a host's logs (journald on Linux, Security log on Windows) for a pattern since a timestamp.",
         {"type": "object",
          "properties": {"host": {"type": "string"},
                         "since": {"type": "string"},
                         "pattern": {"type": "string"}},
          "required": ["host"]},
         _tool_query_logs),
    Tool("list_connections",
         "List current TCP connections on a host (ss on Linux, Get-NetTCPConnection on Windows).",
         {"type": "object",
          "properties": {"host": {"type": "string"}},
          "required": ["host"]},
         _tool_list_connections),
    Tool("list_processes",
         "List processes on a host, optionally filtered by a regex (ps on Linux, Get-Process on Windows).",
         {"type": "object",
          "properties": {"host": {"type": "string"}, "filter": {"type": "string"}},
          "required": ["host"]},
         _tool_list_processes),
    Tool("examine_process",
         "Pull cmdline, status, environ, maps and open fds for one process by pid.",
         {"type": "object",
          "properties": {"host": {"type": "string"}, "pid": {"type": "integer"}},
          "required": ["host", "pid"]},
         _tool_examine_process),
    Tool("pcap_slice",
         "Capture a short pcap on a host via tcpdump with a BPF filter and duration cap (seconds).",
         {"type": "object",
          "properties": {"host": {"type": "string"}, "bpf": {"type": "string"},
                         "duration_s": {"type": "integer"}},
          "required": ["host"]},
         _tool_pcap_slice),
    Tool("isolate_host",
         "Cut a host off the network ('egress' or 'full'). DRY-RUN unless DEFENSE_ALLOW_RESPONSE=1 and dry_run=false.",
         {"type": "object",
          "properties": {"host": {"type": "string"},
                         "mode": {"type": "string", "enum": ["egress", "full"]},
                         "dry_run": {"type": "boolean"}},
          "required": ["host"]},
         _tool_isolate_host),
    Tool("collect_artifact",
         "Pull a file from a host into the engagement artifacts dir and record its SHA-256.",
         {"type": "object",
          "properties": {"host": {"type": "string"}, "path": {"type": "string"}},
          "required": ["host", "path"]},
         _tool_collect_artifact),
    Tool("read_authentication_events",
         "Read authentication events (ssh/sudo on Linux; 4624/4625/4648/4672/4720 on Windows) since a timestamp.",
         {"type": "object",
          "properties": {"host": {"type": "string"}, "since": {"type": "string"}},
          "required": ["host"]},
         _tool_read_auth_events),
    Tool("engagement_log",
         "Append an operator note to the engagement log. Record decisions and context in real time.",
         {"type": "object",
          "properties": {"action": {"type": "string"}, "note": {"type": "string"}},
          "required": ["action"]},
         _tool_engagement_log),
]


def all_tools() -> list[Tool]:
    return list(_TOOLS)


def dispatch(name: str, arguments: dict, scope, backend: Backend,
             engagement: Engagement) -> dict:
    for tool in _TOOLS:
        if tool.name == name:
            try:
                return tool.handler(arguments or {}, scope, backend, engagement)
            except scope_mod.ScopeDenied as exc:
                return _err(f"scope denied: {exc}")
            except (KeyError, TypeError, ValueError) as exc:
                return _err(f"argument error for {name}: {exc}")
    return _err(f"unknown tool: {name}")
