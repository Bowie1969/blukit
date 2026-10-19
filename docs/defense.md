# The `defense` MCP server

Blukit's launcher writes the authorised scope into `.mcp.json`'s
`defense` server `env` block and `exec`s the Claude Code CLI. This
document describes the `defense` server itself: the MCP stdio server
that lives inside this repo and is launched by that `.mcp.json`
entry.

The server is Python 3.11 standard library only — no runtime
dependencies.

## Launch

```json
"mcpServers": {
  "defense": {
    "command": "python3",
    "args": ["-m", "defense"],
    "env": {
      "DEFENSE_ALLOWED_TARGETS": "10.50.0.0/24,bwl-portal",
      "DEFENSE_DENIED_TARGETS": "",
      "DEFENSE_RUN_DIR": "/var/lib/engagements/2026-10-19-blackwood",
      "DEFENSE_BACKEND": "subprocess",
      "DEFENSE_SSH_USER": "soc",
      "DEFENSE_WIN_HOSTS": "bwl-paralegal",
      "DEFENSE_ALLOW_RESPONSE": "0"
    }
  }
}
```

`defense-server` is also installed as a console script by the project
install, equivalent to `python -m defense`.

## Environment contract

| Variable | Purpose |
| --- | --- |
| `DEFENSE_ALLOWED_TARGETS` | Comma-separated allow list (CIDRs, host-IPs, hostnames). Written by blukit. |
| `DEFENSE_DENIED_TARGETS` | Comma-separated deny list. Checked before allow. |
| `DEFENSE_RUN_DIR` | Engagement directory. `events.jsonl` and `log.md` are written here. |
| `DEFENSE_BACKEND` | `subprocess` (default) or `mock` (tests). |
| `DEFENSE_SSH_USER` | Remote user for the SSH backend. Default `root`. |
| `DEFENSE_WIN_HOSTS` | Comma-separated hosts/IPs to treat as Windows (OpenSSH + pwsh). |
| `DEFENSE_ALLOW_RESPONSE` | `1` to let `isolate_host` enforce; otherwise it stays DRY-RUN. |

## Scope gate

`DEFENSE_ALLOWED_TARGETS` is the fence. Every tool call is checked
against it before any backend call is made. Deny wins over allow.
Empty allow refuses every target (fail-closed). Hostname matching is
case-insensitive and strips a trailing dot. A bare host IP in the
allow list is lifted to its host-prefix network.

## Tools

| Tool | Linux call | Windows call |
| --- | --- | --- |
| `query_logs(host, since, pattern)` | `journalctl --since … --grep …` | `Get-WinEvent -LogName Security` filtered |
| `list_connections(host)` | `ss -tnp` | `Get-NetTCPConnection` |
| `list_processes(host, filter?)` | `ps auxww` (filtered locally) | `Get-Process | Where-Object { … }` |
| `examine_process(host, pid)` | `/proc/<pid>/{cmdline,status,environ,maps,fd}` | `Get-Process -Id … + Win32_Process` |
| `pcap_slice(host, bpf, duration_s)` | `sudo -n tcpdump -i any -w /tmp/... -G N -W 1 <bpf>` | — (v1 Linux only) |
| `isolate_host(host, mode, dry_run)` | `sudo -n iptables -I {OUTPUT|INPUT} -j DROP` | `New-NetFirewallRule -Action Block` |
| `collect_artifact(host, path)` | `cat <path>` over SSH, SHA-256 recorded | same via OpenSSH |
| `read_authentication_events(host, since)` | `journalctl -u ssh.service -u sudo --since …` | `Get-WinEvent -LogName Security` filtered to 4624/4625/4648/4672/4720 |
| `engagement_log(action, note)` | append to `events.jsonl` and `log.md` | same |

Every tool call appends one JSON line to `events.jsonl` and one
bullet to `log.md`.

## Response tier (`isolate_host`)

`isolate_host` runs DRY-RUN unless **both** are true:

- `dry_run: false` in the call arguments
- `DEFENSE_ALLOW_RESPONSE=1` in the server env

A DRY-RUN response prints the exact argv that would be executed —
useful for review before you flip the env variable.

## Backends

- `subprocess` (default): wraps `ssh` with `BatchMode=yes` and
  `StrictHostKeyChecking=accept-new`. Argv-only — no `shell=True`.
- `mock`: an in-process backend for tests. Responses are seeded on
  `MockBackend.responses`; calls are captured on `MockBackend.calls`.
  Select with `DEFENSE_BACKEND=mock`.

## Invariants

- The server never dispatches a tool call for a host outside
  `DEFENSE_ALLOWED_TARGETS`, nor one inside `DEFENSE_DENIED_TARGETS`.
- `pcap_slice` refuses a BPF filter that contains a shell
  metacharacter.
- `isolate_host` cannot enforce without both the env flag and the
  per-call opt-in.
- Every tool call is logged. There is no silent path.

## Known limits (v1)

- Windows WinRM is out of scope; Windows requires OpenSSH Server +
  PowerShell on the target.
- `pcap_slice` is Linux-only in v1.
- `query_logs` time windows on Windows are fixed at the last hour;
  the `since` argument is honoured on Linux via `journalctl --since`.
- No log store is maintained by the server itself. Telemetry
  ingestion is a separate deliverable (P1).
