---
name: blukit
description: Start a red-vs-blue engagement from just the asset it protects (an IP range, API, or webapp) by scaffolding the .mcp.json, launch a scope-gated Claude Code session against an authorized target, and inspect, change, or prove the scope blukit recorded. Use when the user asks to start or scaffold a scoped defence/blue-team session, pin the model for one, check what scope is recorded in a project's .mcp.json, or verify the blukit install.
---

# blukit

blukit is a scope-gated launcher for the Claude Code CLI. It records the authorized
targets into the `defense` MCP server's `DEFENSE_ALLOWED_TARGETS` in the nearest
`.mcp.json`, exports the same value, and only then `exec`s the Claude Code CLI with
your arguments.

It **fails closed**. If the scope cannot be recorded — no `.mcp.json`, no `defense`
server in it, an unusable scope — blukit does not launch unless you explicitly pass
`--allow-unscoped`, and it says so on stderr.

## When to use it

- The user wants to start a red-vs-blue engagement and has only named the asset it
  protects (an IP range, an API, or a webapp) — scaffold it with `init`.
- The user wants a scope-gated Claude Code session against an authorized target.
- The user wants to see or change what scope is recorded in a project.
- The user wants to check that blukit, its theme, or its `defense` wiring is healthy.

Record the scope the engagement actually covers and nothing wider. Recording a
broader scope than the user authorized is exactly the failure this tool exists to
prevent, so if the authorized range is unclear, ask before launching.

## Start an engagement (from the target alone)

At the start of a red-vs-blue engagement the only thing you have to give blukit is
the asset it protects — an IP, a CIDR, or a webapp/API URL. `init` scaffolds a
fresh `.mcp.json` wiring the `defense` server to that scope, so there is no file to
hand-build first:

```bash
blukit init -s https://app.lan:8443/api     # scaffold from a webapp URL (records host app.lan)
blukit init -s 10.66.66.10/32               # scaffold from an IP/CIDR
blukit run --init -s 10.66.66.10/32 -y -- --model opus   # scaffold if needed, then launch
```

A scope target may be a URL or `host:port` — the scheme, port and path are dropped
and only the host is recorded (the defense server gates on host/IP). `init` fails
closed on an existing `.mcp.json`: it refuses to overwrite one unless you pass
`--force` (which backs the old file up first). Point at a specific defense server
with `--defense-server PATH` (or `$BLUKIT_DEFENSE_SERVER`) and set the telemetry
directory with `--run-dir PATH` (`DEFENSE_RUN_DIR`).

## Launch

```bash
blukit -s <targets> -y -- --model opus      # record the scope, pin the model, launch
blukit -s 10.0.0.0/24 -y               # record the scope, launch with defaults
blukit -s 10.0.0.7/32,app.lan -y       # comma-separated hosts, IPs, CIDRs or URLs
blukit -s https://app.lan/api -y       # a webapp/API URL, reduced to its host
blukit -s solo                         # the locked-down loopback default
```

Everything after `--` is passed straight through to the Claude Code CLI
(`--model`, `-c`, ...). Use `-y` for non-interactive use. `blukit -n -s ...` is a dry
run: it reports what it would record and launch, and does neither.

## Inspect and change the recorded scope

```bash
blukit scope show      # print the scope recorded in the nearest .mcp.json
blukit scope set ...   # replace it        (add / remove widen or narrow it)
blukit scope clear     # reset to the locked-down default
```

## Check the install

```bash
blukit doctor          # claude binary, config, .mcp.json, theme, audit log
blukit log             # every launch recorded locally
blukit watch           # watch the recorded scope and alarm if it changes
```

## Notes

- The nearest `.mcp.json` at or above the working directory is the one rewritten,
  atomically and with a timestamped backup; `--mcp-json PATH` targets a specific
  file instead.
- `--no-write` leaves `.mcp.json` alone and only exports the environment — useful
  to resume a session (`blukit --no-write -- -c`).
- A project's `.mcp.json` names MCP servers that will actually run, so it is code you
  did not necessarily write. Read it before launching.
- blukit ships a colour theme for the Claude Code TUI (`blukit theme ...`, installed
  to `~/.claude/themes/blukit.json`).
