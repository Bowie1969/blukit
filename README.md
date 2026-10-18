# blukit

**A scope-gated launcher for the Claude Code CLI** — records the authorized defense scope into
`.mcp.json`, pins the model, logs every launch, and only then hands over to Claude. It fails closed:
if the scope can't be recorded, nothing launches.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Version](https://img.shields.io/badge/version-0.1.0-4c1)
![Tests](https://img.shields.io/badge/tests-361%20passing-brightgreen)
![Dependencies](https://img.shields.io/badge/dependencies-none-9cf)
![License](https://img.shields.io/badge/license-BlueKit%20Commercial%20License-lightgrey)

## What it is

blukit is the blue-team mirror of [evilkit](https://github.com/Bowie1969/evilkit), its red-team
counterpart. Where evilkit gates an offensive Kimi Code session, blukit gates a defensive Claude
Code session against the asset you are protecting.

On every launch, before the Claude Code CLI ever starts, blukit does three things:

1. **Collects and normalises a scope** from `--scope`, an interactive prompt, or a configured
   default — hosts, IPs, CIDRs, webapp/API URLs, wildcards and ranges.
2. **Records it atomically** into `DEFENSE_ALLOWED_TARGETS` (and `DEFENSE_DENIED_TARGETS` with
   `--deny`) of the `defense` MCP server in the nearest `.mcp.json`, with a timestamped backup.
3. **Appends a line** to a local, append-only launch log.

Then it `exec`s the Claude Code CLI with your arguments — refusing to launch unless you have
explicitly pinned a model.

Why write the file instead of just exporting a variable: the `env` block of `.mcp.json` is injected
into the MCP child process and wins over the parent environment, so an exported
`DEFENSE_ALLOWED_TARGETS` never reaches the tool server. blukit writes the file, so the scope in
force is the one you authorised for *this* session — not the one left over from last week.

### What blukit is not

- It does **not** include, ship, or proxy any AI model or API access. You bring your own Claude Code
  CLI installation and credentials.
- It is **not** affiliated with, endorsed by, or produced by Anthropic or the Claude Code CLI.
- Its scope validation is a **typo guard, not a security boundary**. The real enforcement lives in
  the `defense` tool server you supply, which reads the scope from its own launch environment.

## Highlights

- **Fails closed.** No `.mcp.json`, no `defense` server in it, an unreadable file, or a failed
  write → nothing launches. `--allow-unscoped` is the only escape hatch, and it says so on stderr.
- **Atomic, backed-up writes.** A candidate file is staged, parsed, revalidated, then `os.replace`d
  into place; the previous version is kept as a timestamped `.bak`. A project is never left with a
  half-written or missing `.mcp.json`.
- **Mandatory model pin.** Everything after `--` passes through to the Claude Code CLI, and a real
  launch is refused unless those arguments name a model (`-- --model opus`). blukit never inherits
  an ambient default model.
- **Typo-guard scope parsing.** IPs, CIDRs, hostnames, URLs (`https://app.lan:8443/api` → `app.lan`),
  wildcards (`192.168.1.*` → `/24`) and ranges (`10.0.0.1-10.0.0.50`). Range expansion can never
  widen a scope, and a fat-fingered token is rejected before it's written anywhere.
- **Deny-list support.** `--deny` writes `DEFENSE_DENIED_TARGETS`; the server checks deny before
  allow, so "everything in range except this one host" works.
- **Append-only launch log.** `blukit log` answers "what did I point this at, and when?" — each
  entry carries an `enforced` flag, written to a mode-`0600` file.
- **`blukit watch`.** A live pane that samples the recorded scope and raises an alarm the moment it
  changes under you. An engagement that quietly widens is worse than one that fails.
- **Zero runtime dependencies.** Python 3.11 standard library only.

## The fail-closed scope-recording doctrine

The design rule blukit is built around: **the boundary that matters is the one in force when the
agent runs, and it must be the one the operator authorised — or nothing runs.**

- The scope is always recorded in `.mcp.json`, because that is the only value the `defense` tool
  server actually reads. Exported environment variables do not survive into the MCP child process.
- If the scope cannot be recorded, blukit refuses to launch. Launching anyway would mean the session
  ran under whatever scope the file already held — possibly wider than the command line, silently.
- The model must be pinned per launch. A blue session that falls back to an ambient default model is
  a session whose reasoning you did not choose.
- Observation is the default; action is opt-in. The response tier (`block-host`) is off unless the
  operator sets `DEFENSE_ALLOW_RESPONSE=1` and configures an enforcement command — the launched
  agent runs unprivileged on purpose, because the analyst that can act on the network is the analyst
  that can be talked into acting on the wrong thing.

## Requirements

- Python 3.11 or newer.
- The Claude Code CLI, installed and authenticated separately — blukit launches it and never
  installs, configures, or authenticates it.
- A project that declares a `defense` MCP server in its `.mcp.json`, or a fresh one scaffolded with
  `blukit init`. See [`examples/.mcp.json.example`](examples/.mcp.json.example) for the shape blukit
  expects. The `defense` tool server itself ships with your lab, not with this launcher — blukit
  writes the file that points at it.

## Install

```bash
git clone https://github.com/Bowie1969/blukit.git
cd blukit
./install.sh        # uses uv or pipx if present, otherwise a private venv
blukit doctor       # verify the install
```

`install.sh` also drops the bundled colour theme into `~/.claude/themes/`. Or install directly from
the checkout:

```bash
python3 -m pip install .     # or: uv tool install .  /  pipx install .
```

## Quick start

Start an engagement from the target alone — no file to hand-build:

```bash
blukit init -s https://app.lan:8443/api                  # scaffold .mcp.json (records host app.lan)
blukit run --init -s 10.66.66.10/32 -y -- --model opus   # scaffold if needed, then launch
```

Record a scope into an existing project and launch:

```bash
blukit -s 10.0.0.0/24 -y -- --model opus
blukit -s 10.0.0.7/32,app.lan -y -- --model sonnet
blukit -s solo -- --model opus                           # locked-down default (127.0.0.1/32)
blukit -s 10.0.0.0/24 --deny 10.0.0.5/32 -y -- --model opus
blukit --no-write -- --model opus -c                     # leave .mcp.json alone; resume a session
blukit -n -s 10.0.0.0/24 -- --model opus                # dry run: report only, write/launch nothing
```

Inspect, change, and verify:

```bash
blukit scope show        # print the recorded scope
blukit scope add 10.0.0.16/28
blukit scope remove 10.0.0.0/24
blukit scope set app.lan
blukit scope clear       # reset to 127.0.0.1/32
blukit log               # every launch recorded locally
blukit watch             # watch the scope and alarm if it changes
blukit doctor            # check claude binary, config, .mcp.json, theme, log
```

## Commands

| Command | What it does |
| --- | --- |
| `blukit run` | *default* — record the scope and launch the Claude Code CLI |
| `blukit init` | scaffold a wired `.mcp.json` from the target alone |
| `blukit scope show/set/add/remove/clear` | inspect or change the recorded scope |
| `blukit log` | list launches recorded locally (`--json` for raw lines) |
| `blukit watch` | live pane that alarms when the scope changes (`--once` for a single frame) |
| `blukit doctor` | verify the installation |
| `blukit theme install/status` | manage the bundled colour theme |
| `blukit config path/show/init` | show or write configuration |
| `blukit agent --list` | list the built-in defense agent configs |
| `blukit version` | print the version |

`run` flags: `-s/--scope`, `--deny`, `--init`, `--defense-server`, `--run-dir`, `--mcp-json`,
`--no-write`, `-y/--yes`, `-n/--dry-run`, `--allow-unscoped`, `--allow-any`.

## Architecture

```
                        ┌────────────────────────────────────┐
  operator              │              blukit CLI             │
  -s <scope> ─────────▶ │   argparse → command dispatch       │
  -- --model <m>        └───┬─────────────┬──────────────┬────┘
                            │             │              │
                   ┌────────▼──────┐ ┌────▼───────┐ ┌────▼───────────┐
                   │   scope.py    │ │ mcpscope.py│ │    audit.py     │
                   │  parse &      │ │  atomic    │ │  append-only    │
                   │  normalise    │ │  write     │ │  launch log     │
                   └────────┬──────┘ └────┬───────┘ └────────────────┘
                            │             │
                            │    ┌────────▼──────────┐
                            │    │     .mcp.json      │  DEFENSE_ALLOWED_TARGETS
                            │    │  mcpServers:       │  DEFENSE_DENIED_TARGETS
                            │    │    defense         │  DEFENSE_ALLOW_RESPONSE
                            │    └────────┬───────────┘
                            │             │  (env injected into the child process)
                            │    ┌────────▼───────────┐
                            │    │  defense server     │  you supply it;
                            │    │  telemetry + gate   │  blukit wires the file
                            │    └────────────────────┘
                            │
                   ┌────────▼───────────┐
                   │ execvpe(claude, …) │  model pinned, scope exported
                   └────────────────────┘
```

- **`scope.py`** — parses and normalises the scope string. A URL is reduced to its host; wildcards
  and ranges expand to CIDRs; anything a fat-fingered or injected scope could hide behind is
  rejected loudly.
- **`mcpscope.py`** — reads and rewrites `.mcp.json` under an advisory lock: stage, revalidate,
  back up, `os.replace`. Never leaves a half-written file.
- **`audit.py`** — appends one JSON line per launch to `~/.local/state/blukit/launches.jsonl`
  (mode `0600`), recording the scope, whether it was enforced, and the target file.
- **`watch.py`** — samples the recorded scope and reports changes, distinguishing a provable
  widening (alarm) from a plain change (note).
- **`response.py`** — the `block-host` decision tier. It decides and explains; it never executes.
  Off unless `DEFENSE_ALLOW_RESPONSE=1`.

## Configuration

`blukit config init` writes a commented `~/.config/blukit/config.toml`:

```toml
claude_bin = "claude"
default_scope = "127.0.0.1/32"
backup_keep = 5
write_mcp_json = true
audit = true
audit_path = ""
```

Environment variables: `BLUKIT_CLAUDE_BIN`, `BLUKIT_DEFENSE_SERVER`, `BLUKIT_CONFIG`,
`BLUKIT_VENV_DIR`, `BLUKIT_CLAUDE_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `DEFENSE_RUN_DIR`.

## Security notes

- **Don't commit `.mcp.json`.** It holds the scope you're authorised to test — often a client's
  address range — and possibly a tool-server token. This repo's `.gitignore` covers `*.mcp.json` and
  its `.bak` / `.new` / `.lock` sidecars; copy it into your own projects.
- **A project's `.mcp.json` is code you did not write.** The Claude Code CLI executes the `command`
  in the `defense` server entry. Read that file before running blukit in a cloned repository, and
  look at what `command` and `args` point at.

## License

blukit is proprietary, source-available software under the
[**BlueKit Commercial License**](LICENSE) — licensed, not sold. See [`LICENSE`](LICENSE) for the
binding terms and [`docs/pricing.md`](docs/pricing.md) for how it is sold.

## Authorized use / defensive-operations disclaimer

Use blukit only to defend systems you are authorized to operate. You are responsible for having
written permission for every target you put in a scope, and for complying with the terms of service
of the Claude Code CLI and any other third-party software involved. blukit is not affiliated with,
endorsed by, or produced by Anthropic or the Claude Code CLI. The software is provided as is,
without warranty, and the licensor's liability is limited as set out in the [license](LICENSE).
