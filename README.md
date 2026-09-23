# blukit

A scope-gated launcher for the Claude Code CLI.

blukit records two things — the authorized target scope, in the right place, and a line in a local
launch log — then hands over to the Claude Code CLI. On each launch it collects a scope from
`--scope`, an interactive prompt, or a configured default, validates and normalises it, writes it
into the `DEFENSE_ALLOWED_TARGETS` value of the `defense` MCP server in the nearest `.mcp.json`
(atomically, with a timestamped backup), exports it, and `exec`s the Claude Code CLI with your
arguments. It also ships a colour theme for the Claude Code TUI and a `doctor` command.

The point is the thing you would otherwise forget or mistype: a defense tool server takes its
allowed targets from the `env` block of `.mcp.json`, and that block overrides whatever the parent
shell exported. blukit writes the file, so the scope is always the one you authorised for this
session rather than the one left over from last week. `--deny` writes the matching denied-target
list into the same block, for the "everything in this range except this one host" case.

It fails closed. If the requested scope cannot be recorded — there is no `.mcp.json`, the file
declares no `defense` server, the file cannot be read, or the write does not succeed — blukit stops
and starts nothing. Launching anyway would mean the session ran under whatever scope the file
already held, which is not the one on the command line. `--allow-unscoped` opts back into that, and
says so when it happens.

It also pins the model. Everything after `--` is passed straight to the Claude Code CLI, and a real
launch is refused unless those arguments name a model explicitly — `-- --model opus` or `-- --model
sonnet`. blukit never inherits an ambient default model: a blue session always runs on the model you
chose for it, per launch. This is the one place blukit's launch path deliberately differs from
[evilkit](https://github.com/Bowie1969/evilkit), its red-team counterpart, which hands its
passthrough to the Kimi Code CLI unconditionally. A `--dry-run` launches nothing, so it is exempt.

## What blukit is not

- **blukit does not include, ship, or proxy any AI model or API access.** You bring your own Claude
  Code CLI installation and your own account and credentials. blukit sells the launcher and the
  workflow, not API capacity.
- **blukit is not affiliated with, endorsed by, or produced by Anthropic or the Claude Code CLI.**
  Those are third-party products with their own terms, which you must comply with yourself.
- **The scope validation is a typo guard, not a security boundary.** It stops a fat-fingered
  address from being written anywhere. The real enforcement lives in the defense tool server you
  configure, which reads the scope from its own launch environment.

## Requirements

- Python 3.11 or newer.
- The Claude Code CLI, installed and authenticated separately. blukit launches it and never installs,
  configures, or authenticates it.
- A project that declares a `defense` MCP server in its `.mcp.json`. See
  [`examples/.mcp.json.example`](examples/.mcp.json.example) for the shape blukit expects. If the
  project is not yours, read that file before you run blukit in it — see
  [A project's `.mcp.json` is code you did not write](#a-projects-mcpjson-is-code-you-did-not-write).

## Install

From the root of your blukit checkout:

```bash
./install.sh
```

The script needs `python3`, refuses to run on Python older than 3.11, and installs the CLI with `uv`
if it is present, otherwise `pipx`, otherwise into a private virtual environment at
`~/.local/share/blukit/venv`, whose `blukit` script is linked into `~/.local/bin`. The virtual
environment is the fallback rather than `python3 -m pip install --user` because a plain `--user`
install is refused on externally-managed Pythons (Debian, Ubuntu, Parrot) under PEP 668; the `--user`
path is still used, last, on machines that have no `venv` module either. It then copies the colour
theme into `~/.claude/themes/` when `~/.claude` exists. It is safe to re-run.

Manual alternatives, if you would rather not use the script:

```bash
uv tool install .
```

```bash
pipx install .
```

Or, straight from the checkout without installing anything, using the project root as the working
directory:

```bash
python3 -m blukit --help
```

## Quick start

### Start an engagement from the target alone

At the start of a red-vs-blue engagement the only thing you need to give blukit is the asset it
protects — an IP range, an API, or a webapp. `blukit init` scaffolds a fresh `.mcp.json` that wires
the `defense` server to that scope, so there is no file to hand-build first:

```console
$ cd /srv/engagements/acme
$ blukit init -s https://app.acme.internal:8443/api
blukit: scaffolded /srv/engagements/acme/.mcp.json with scope app.acme.internal
blukit: defense server -> defense_server.py
blukit: launch it with, e.g., blukit -y -- --model opus
```

A scope target may be a URL or `host:port`: the scheme, port and path are dropped and only the host
is recorded, because the `defense` server gates on host and IP. `init` fails closed on an existing
`.mcp.json` — it refuses to overwrite one unless you pass `--force` (which backs the old file up
first). Point at a specific server binary with `--defense-server PATH` (or `$BLUKIT_DEFENSE_SERVER`)
and name the telemetry directory with `--run-dir PATH`. To scaffold and launch in one step, use
`blukit run --init -s <target> -- --model opus`, which creates the file only when none exists.

### Record a scope into an existing project

Assume your project is `/srv/engagements/acme` and its `.mcp.json` contains the example `defense`
server. Check what the run would do before it does it:

```console
$ cd /srv/engagements/acme
$ blukit -s 10.20.30.0/24 -n
blukit: would record 10.20.30.0/24 in /srv/engagements/acme/.mcp.json
blukit: would launch /home/you/.claude/bin/claude
```

The dry run inspects the same target a real run would use: a missing `.mcp.json`, a file with no
`defense` server, and unreadable JSON each produce the same warning and the same exit code they
would in a real run, which is `1`. Only the file mutation is skipped. One thing it cannot tell you is
whether the destination directory is writable, which a real run discovers when it tries to stage the
new file.

Drop `-n` to actually do it:

```console
$ blukit -s 10.20.30.0/24 -- --model opus
blukit: scope 10.20.30.0/24 recorded in /srv/engagements/acme/.mcp.json
blukit: authorized scope -> 10.20.30.0/24
```

That `authorized scope` line is a guarantee: it is printed only when the scope is genuinely in force
for the session. The `env` block of `.mcp.json` changed from the shipped loopback default to your
authorised network:

```diff
       "env": {
-        "DEFENSE_ALLOWED_TARGETS": "127.0.0.1/32",
+        "DEFENSE_ALLOWED_TARGETS": "10.20.30.0/24",
         "DEFENSE_DENIED_TARGETS": "",
```

and the previous version was copied beside it, timestamped:

```text
.mcp.json
.mcp.json.20260916-113217.bak
.mcp.json.lock
```

The backup is a copy, so the target is never briefly absent, and the target's permissions are
preserved, so a `.mcp.json` you hardened to `0600` stays `0600`. `.mcp.json.lock` is the sidecar lock
that serialises concurrent writes to the same file; it appears on the first write and stays there,
and this repository's `.gitignore` covers it.

No other value is changed, though the first write also reformats the file to two-space indentation,
so a diff in your editor will show more than the single line above. Run it again with the same scope
and blukit leaves the file alone and creates no further backup:

```console
$ blukit -s 10.20.30.0/24 -- --model opus
blukit: scope already 10.20.30.0/24 in /srv/engagements/acme/.mcp.json
blukit: authorized scope -> 10.20.30.0/24
```

If the scope cannot be recorded, nothing launches:

```console
$ blukit -s 10.20.30.0/24 -y -- --model opus
blukit: no .mcp.json here or in any parent directory; nothing to scope
blukit: nothing was launched: the scope could not be recorded
blukit: pass --allow-unscoped to launch anyway, knowing the scope may differ
$ echo $?
1
```

To create the missing file from the target instead of launching unscoped, run `blukit init -s
<target>` (or `blukit run --init -s <target> -- --model opus` to scaffold and launch together).

`--allow-unscoped` restores the older behaviour of launching anyway. It prints the requested scope
but marks it as not enforced, because the `.mcp.json` `env` block still wins for the tool server:

```console
$ blukit -s 10.20.30.0/24 -y --allow-unscoped -- --model opus
blukit: no .mcp.json here or in any parent directory; nothing to scope — launching with the scope NOT recorded
blukit: the enforced scope may differ from the one requested; check .mcp.json before trusting it
blukit: scope -> 10.20.30.0/24 (NOT enforced; recorded scope is unchanged)
```

Read that as: the session starts, and the targets it can actually reach are the ones already recorded
in the file, which may cover more addresses than the range you just asked for. Use it when you have
checked the file yourself, not to get past an error you do not understand.

Running `blukit` with no arguments is the same as `blukit run`: it asks for a scope and launches.
The prompt defaults to the locked-down loopback scope, so pressing enter scopes you to your own
machine and nothing else:

```console
$ blukit
scope [enter = 127.0.0.1/32]> 10.20.30.7
blukit: scope 10.20.30.7/32 recorded in /srv/engagements/acme/.mcp.json
blukit: authorized scope -> 10.20.30.7/32
```

A scope the parser cannot make sense of is rejected before anything is written:

```console
$ blukit -s '10.20.30.0/24; rm -rf /' -y
blukit: '10.20.30.0/24; rm -rf /' contains characters that are not valid in a target
blukit: expected a comma-separated list of hosts, IPs, CIDRs or webapp/API URLs, or 'solo'
$ echo $?
2
```

## Command reference

```console
$ blukit --help
usage: blukit [-h] [-V] COMMAND ...

Scope-gated launcher for the Claude Code CLI.

positional arguments:
  COMMAND
    run          set the scope and launch the Claude Code CLI (default)
    init         scaffold a .mcp.json wiring the defense server, from the target alone
    scope        inspect or change the recorded scope
    log          list the launches recorded locally
    watch        watch the recorded scope and alarm if it changes
    doctor       check the local installation
    theme        manage the bundled colour theme
    config       show configuration
    version      print the version

options:
  -h, --help     show this help message and exit
  -V, --version  show program's version number and exit

examples:
  blukit -s 10.0.0.0/24 -y -- --model opus
                                        scope one network, pin the model, launch
  blukit -s 10.0.0.5,app.lan -y -- --model sonnet
                                        non-interactive, two assets, pinned model
  blukit -s solo -- --model opus        accept the locked-down default, pin the model
  blukit --no-write -- --model opus -c  leave .mcp.json alone, resume a pinned session
  blukit scope show                     print the scope recorded in .mcp.json
  blukit scope add 10.0.0.0/24          widen the record by one network
  blukit scope remove 10.0.0.0/24       drop one network from the record
  blukit log                            list the launches recorded locally
  blukit watch                          watch the scope and alarm if it changes
  blukit doctor                         check that everything is wired up

Every argument after `--` is passed straight through to the Claude Code CLI.
A launch must pin a model explicitly (e.g. `-- --model opus`): blukit never inherits
an ambient default model, so a run with no model is refused before anything is
written or launched.
```

Any first argument that is not one of the commands above is treated as an argument to `run`, so
`blukit --scope 10.0.0.0/24` and `blukit run --scope 10.0.0.0/24` are the same thing.

| Flag | Effect |
|---|---|
| `-h`, `--help` | Print help and exit `0`. |
| `-V`, `--version` | Print `blukit 0.1.0` and exit `0`. |

### `blukit run`

```console
$ blukit run --help
usage: blukit run [-h] [-s TARGETS] [--init] [--defense-server PATH]
                  [--run-dir PATH] [--deny TARGETS] [--mcp-json PATH]
                  [--no-write] [-y] [-n] [--allow-unscoped] [--allow-any]
                  [--strict] [--no-audit]

Set the authorized scope, then hand over to the Claude Code CLI.

options:
  -h, --help            show this help message and exit
  -s, --scope TARGETS   comma-separated hosts, IPs, CIDRs or webapp/API URLs
                        (a URL is reduced to its host); 'solo' for the locked-
                        down default
  --init                scaffold a .mcp.json wiring the defense server if none
                        exists, then launch
  --defense-server PATH
                        path recorded for the defense tool server when
                        scaffolding (default: $BLUKIT_DEFENSE_SERVER or
                        'defense_server.py')
  --run-dir PATH        telemetry run directory to record (DEFENSE_RUN_DIR)
                        when scaffolding
  --deny TARGETS        comma-separated targets to refuse even inside the
                        scope; an empty value denies nothing (omit it to leave
                        the record alone)
  --mcp-json PATH       path to .mcp.json (default: nearest one at or above
                        the working directory)
  --no-write, --read-only
                        do not modify .mcp.json; only export the environment
  -y, --yes             never prompt; fall back to the configured default
                        scope
  -n, --dry-run         report what would happen without writing or launching
  --allow-unscoped      launch even when the requested scope could not be
                        recorded
  --allow-any           permit a /0 scope, which authorises every address
  --strict              deprecated; an unrecordable scope is already fatal
                        unless --allow-unscoped
  --no-audit            do not append this launch to the local log
```

The same `examples:` and `--` epilog that `blukit --help` prints follows this output; it is omitted
here.

| Flag | Effect |
|---|---|
| `-s`, `--scope TARGETS` | Use this scope instead of prompting. Commas separate targets, so quote the value in your shell. A target may be a webapp/API URL or `host:port`, which is reduced to its host. |
| `--init` | If no `.mcp.json` is found, scaffold one wiring the `defense` server to this scope before launching (see [`blukit init`](#blukit-init)). An existing file is used as-is and never overwritten. |
| `--defense-server PATH` | Path recorded for the defense tool server when `--init` scaffolds a file. Defaults to `$BLUKIT_DEFENSE_SERVER`, else `defense_server.py`. |
| `--run-dir PATH` | Telemetry run directory (`DEFENSE_RUN_DIR`) recorded when `--init` scaffolds a file. Defaults to the `DEFENSE_RUN_DIR` in the environment, else blank. |
| `--deny TARGETS` | Record this as the denied-target list, parsed and normalised exactly like `--scope`, including the range forms. The tool server checks it first, so a target can be inside the scope and still be refused. Omitting the flag leaves whatever deny list is recorded in place. An empty value records "deny nothing" — it is not the `solo` loopback alias. |
| `--mcp-json PATH` | Record the scope in this file instead of searching upward. A path that does not exist is a fatal error (`exit 1`). |
| `--no-write`, `--read-only` | Leave `.mcp.json` untouched, only export the environment, and launch. See the limitation below. |
| `-y`, `--yes` | Never prompt. Also implied when stdin is not a TTY. |
| `-n`, `--dry-run` | Validate the scope, inspect the `.mcp.json` it would record into, report what would happen, and stop without writing or launching. It exits `1` where a real run would refuse to start. Combined with `--no-write` it stops earlier and checks no target. |
| `--allow-unscoped` | Launch even when the requested scope could not be recorded. The run then enforces whatever scope the file already holds, which may be wider than the one requested; the banner says so. |
| `--allow-any` | Permit a `/0` scope. Without it, `0.0.0.0/0` and `::/0` are refused. |
| `--strict` | Deprecated and does nothing. Failing closed is the default, so an unrecordable scope is already fatal; the flag is still accepted so existing invocations keep working. |
| `--no-audit` | Skip the launch log entry for this run. The launch itself is unchanged. |
| `--` | Everything after it is passed through to the Claude Code CLI untouched. |

Options intended for the Claude Code CLI must go after `--`. Any leading argument blukit does not
recognise is handed to `run`, which rejects it as an unrecognised argument with `exit 2`:

```console
$ blukit --model claude-k2
usage: blukit [-h] [-V] COMMAND ...
blukit: error: unrecognized arguments: --model claude-k2
$ blukit -- --model claude-k2       # launches the CLI with --model claude-k2
```

### `blukit init`

```console
$ blukit init --help
usage: blukit init [-h] [-s TARGETS] [--defense-server PATH] [--run-dir PATH]
                   [--deny TARGETS] [--mcp-json PATH] [--force] [--allow-any]
                   [-n] [-y]

Write a fresh .mcp.json that wires the defense tool server to the authorized
scope, so an engagement can start from nothing but the asset it protects.
Refuses to overwrite an existing file unless --force.

options:
  -h, --help            show this help message and exit
  -s, --scope TARGETS   the asset being protected: hosts, IPs, CIDRs or
                        webapp/API URLs, comma-separated; 'solo' for the
                        locked-down default
  --defense-server PATH
                        path recorded for the defense tool server (default:
                        $BLUKIT_DEFENSE_SERVER or 'defense_server.py')
  --run-dir PATH        telemetry run directory to record (DEFENSE_RUN_DIR)
  --deny TARGETS        comma-separated targets to refuse even inside the
                        scope
  --mcp-json PATH       where to write .mcp.json (default: ./.mcp.json in the
                        working directory)
  --force               overwrite an existing .mcp.json (the old one is backed
                        up first)
  --allow-any           permit a /0 scope, which authorises every address
  -n, --dry-run         report what would be written without writing it
  -y, --yes             never prompt; fall back to the configured default
                        scope
```

`init` is how an engagement starts when there is no project file yet: give it the asset being
defended and it writes a complete `.mcp.json` with the `defense` server wired to that scope and every
optional capability off. It **fails closed** on an existing file — it never overwrites one unless you
pass `--force`, and even then the previous file is copied to a timestamped `.bak` first, exactly as a
scope rewrite is. The scope may be an IP, a CIDR, or a webapp/API URL; a URL or `host:port` is
reduced to its host, because the `defense` server enforces on host and IP.

| Flag | Effect |
|---|---|
| `-s`, `--scope TARGETS` | The asset being protected. Same grammar as `blukit run -s`, including URLs. |
| `--defense-server PATH` | Path recorded for the `defense` server's `command`/`args`. Defaults to `$BLUKIT_DEFENSE_SERVER`, else `defense_server.py`. |
| `--run-dir PATH` | Value recorded for `DEFENSE_RUN_DIR`. Defaults to the environment's `DEFENSE_RUN_DIR`, else blank. |
| `--deny TARGETS` | Denied-target list to record, parsed like `--scope`. |
| `--mcp-json PATH` | Where to write, instead of `./.mcp.json`. |
| `--force` | Overwrite an existing file (backed up first). Without it, an existing file is a fatal error (`exit 1`). |
| `--allow-any` | Permit a `/0` scope. |
| `-n`, `--dry-run` | Report what would be written and stop. |
| `-y`, `--yes` | Never prompt; also implied when stdin is not a TTY. |

### `blukit scope`

| Command | Effect |
|---|---|
| `blukit scope show` | Print the scope currently recorded in the `.mcp.json` blukit finds, plus the deny list on a second line when one is recorded. Exits `1` when no file or no `defense` server is found. |
| `blukit scope set TARGETS` | Replace the recorded scope with `TARGETS`. |
| `blukit scope add TARGETS` | Add `TARGETS` to the recorded scope. A target already recorded is not repeated. |
| `blukit scope remove TARGETS` | Drop `TARGETS` from the recorded scope. Refused when it would leave no target at all. |
| `blukit scope clear` | Reset the recorded scope to `127.0.0.1/32`. |

All of them accept `--mcp-json PATH`; `set`, `add` and `remove` also accept `--dry-run` and
`--allow-any`. With no action, `blukit scope` behaves as `scope show`. The four writing commands go
through the same validated, atomic, backed-up path `run` uses, so a target list that does not parse
changes nothing.

```console
$ blukit scope show
10.20.30.0/24
$ blukit scope add 10.20.40.0/24
blukit: scope 10.20.30.0/24,10.20.40.0/24 recorded in /srv/engagements/acme/.mcp.json
$ blukit scope clear
blukit: /srv/engagements/acme/.mcp.json reset to 127.0.0.1/32
```

### `blukit log`

Prints the launch log: one line per launch, each naming the scope that launch was authorised for. It
reads `$XDG_STATE_HOME/blukit/launches.jsonl` — `~/.local/state/blukit/launches.jsonl` by default,
or `audit_path` if you set one — and never writes to it.

| Flag | Effect |
|---|---|
| `-n`, `--limit N` | Show the newest `N` entries. The default is `20`; `0` shows every entry. |
| `--path PATH` | Read this file instead of the configured log. |
| `--json` | Print the stored lines exactly as they were written, one per line, oldest first, ready for `jq`. |

The default output is newest-first, one entry per line:

```text
<timestamp>  <scope>  enforced|NOT enforced  <path to the .mcp.json, or - if none>
```

`enforced` means that scope was written into the `.mcp.json` named beside it, so it is the scope in
force for the session. `NOT enforced` means it was not: `--no-write` left the file's existing `env`
block in charge, and `--allow-unscoped` launched with nothing recorded. A `-` in the last column is
an entry with no `.mcp.json` at all, which is what `--allow-unscoped` records when there was no file
to write to. `--json` follows the file instead, oldest first.

```console
$ blukit -s 10.20.30.0/24 -y -- --model opus
blukit: scope 10.20.30.0/24 recorded in /srv/engagements/acme/.mcp.json
blukit: authorized scope -> 10.20.30.0/24
$ blukit -s 10.20.30.9 -y --no-write -- --model opus
blukit: scope -> 10.20.30.9/32 (not written; --no-write)
blukit: scope -> 10.20.30.9/32 (NOT enforced; recorded scope is unchanged)
$ blukit log
2026-09-16T04:24:34Z  10.20.30.9/32  NOT enforced  /srv/engagements/acme/.mcp.json
2026-09-16T04:24:33Z  10.20.30.0/24  enforced  /srv/engagements/acme/.mcp.json
$ blukit log --json
{"v": 1, "at": "2026-09-16T04:24:33Z", "event": "launch", "scope": "10.20.30.0/24", "enforced": true, "mcp_json": "/srv/engagements/acme/.mcp.json", "cwd": "/srv/engagements/acme", "claude": "/home/you/.claude/bin/claude", "pid": 3892270}
{"v": 1, "at": "2026-09-16T04:24:34Z", "event": "launch", "scope": "10.20.30.9/32", "enforced": false, "mcp_json": "/srv/engagements/acme/.mcp.json", "cwd": "/srv/engagements/acme", "claude": "/home/you/.claude/bin/claude", "pid": 3892292}
```

Only a launch that happens is logged, so a dry run, a scope the parser rejected, and a run that
could not find the Claude Code CLI all record nothing. With no log file yet, the command says so and
exits `0` — a fresh install with nothing recorded is a normal state, not an error:

```console
$ blukit log
blukit: no launches recorded yet (/home/you/.local/state/blukit/launches.jsonl)
$ echo $?
0
```

A line that cannot be parsed is skipped and counted on stderr, and the exit code is `1` only when
the log cannot be read at all, or when it holds no readable line.

### `blukit watch`

A live pane that watches the recorded scope and alarms when it changes. It launches nothing, writes
nothing and decides nothing — it exists because `.mcp.json` *is* the boundary, and anything on the
machine can edit it while a session is open.

| Flag | Effect |
|---|---|
| `--mcp-json PATH` | Watch this file instead of the nearest one found from the working directory. |
| `--interval SECONDS` | How often to sample. The default is `1.0`; the floor is `0.1`. |
| `--once` | Print one frame and exit, for a status script or a pipe. |
| `--no-color` | Never emit ANSI colour, even on a terminal. `NO_COLOR` is honoured too. |
| `-n`, `--limit N` | How many recent launches to list. The default is `5`; `0` lists none. |

```console
$ blukit watch
blukit watch                                                         15:03:59
──────────────────────────────────────────────────────────────────────────────
scope in force    10.20.30.0/24
recorded in       /srv/engagements/acme/.mcp.json  (last changed 12m 41s ago)
watching for      12m 44s
──────────────────────────────────────────────────────────────────────────────
ALERTS
  nothing has changed since this pane started
──────────────────────────────────────────────────────────────────────────────
RECENT LAUNCHES
  15:01:15  10.20.30.0/24  enforced
──────────────────────────────────────────────────────────────────────────────
Ctrl-C to stop. This pane only reports; it decides nothing.
```

Then something rewrites the scope — another launcher run, a script, an editor, the agent's own file
tools — and the pane says so at once:

```console
ALERTS (1)
  15:16:40  !!  the recorded scope changed under you: 10.20.30.0/24 ->
                10.0.0.0/8 and it is wider than before
```

An alarm is raised when the scope changes, when the `defense` server stops declaring one, and when
the file is deleted or becomes unreadable. A rewrite that leaves the scope identical is a note, not
an alarm, so an editor saving the file does not look like an incident.

A change is called out as a *widening* only when that can be proved: every address the old scope
authorised is still authorised, and the new scope authorises at least one address the old one did
not. Hostnames, mixed IPv4/IPv6 lists and anything unparseable are reported as a plain change, never
as a direction.

Exit is `0` if the scope held for the whole run and `1` if any alarm was raised, so the same command
works as a check in a script:

```console
$ blukit watch --once --no-color > /dev/null
$ echo $?
0
```

### `blukit doctor`

Reports the resolved Claude Code binary, the config file in use, the `.mcp.json` it found, the scope
recorded there, whether the theme is installed, and the state of the launch log.

```console
$ blukit doctor
claude binary        : /home/you/.claude/bin/claude ok
config file        : /home/you/.config/blukit/config.toml absent (defaults)
mcp.json           : /srv/engagements/acme/.mcp.json
recorded scope     : 10.20.30.0/24
theme installed    : yes (/home/you/.claude/themes)
audit log          : /home/you/.local/state/blukit/launches.jsonl (last launch 2026-09-16T04:24:34Z)
```

The `config file` line distinguishes three states: `ok` when the file was read, `absent (defaults)`
when there is no file at all, and `unreadable (using defaults)` when a file exists but could not be
parsed. Problems with individual keys are printed as `blukit: ...` warnings above the report, and
leave the line as `ok`, because the file itself parsed.

The `audit log` line names the log file and its newest entry, or says `(nothing recorded yet)` or
`disabled` when `audit = false`.

### `blukit theme`

```console
$ blukit theme --help
usage: blukit theme [-h] [--force] [{install,status}]

positional arguments:
  {install,status}

options:
  -h, --help        show this help message and exit
  --force           overwrite an existing theme
```

| Command | Effect |
|---|---|
| `blukit theme install` | Copy the bundled theme to `<claude home>/themes/blukit.json`. A file that is already there is left untouched, the command exits `1` if it differs from the bundled theme, and a destination that is a symlink is always refused. |
| `blukit theme status` | Report whether the theme file is present. This is the default, and it does not distinguish a real file from a symlink. |
| `--force` | Let `install` replace an existing file, including one you have edited yourself. It does not override the refusal to write through a symlink. |

### `blukit config`

```console
$ blukit config --help
usage: blukit config [-h] ACTION ...

positional arguments:
  ACTION
    path      print the config file location
    show      print the effective configuration
    init      write a commented default config file

options:
  -h, --help  show this help message and exit
```

| Command | Effect |
|---|---|
| `blukit config path` | Print the config file location. |
| `blukit config show` | Print the effective configuration and where it came from. This is the default. |
| `blukit config init` | Write a commented default config file. Fails with `exit 1` if the file already exists. |

### `blukit version`, `-V`, `--version`

All three print `blukit 0.1.0`.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. The CLI was launched, or the command did what was asked. |
| `1` | Error: the requested scope could not be recorded (no `.mcp.json`, no `defense` server, unreadable file, failed write), a missing Claude Code binary, a `--mcp-json` path that does not exist, `config init` against an existing file, `theme install` against a modified theme file, `blukit log` against a log it cannot read or that holds no readable line. A dry run exits `1` in the same situations. |
| `2` | Usage error: the scope was rejected by the parser, or an argument was not recognised. |

## Configuration

Config lives in `$XDG_CONFIG_HOME/blukit/config.toml`, which is `~/.config/blukit/config.toml` by
default. `blukit config init` writes this file, with comments, and refuses to overwrite one that
already exists:

```toml
# blukit configuration
# Docs: https://github.com/Bowie1969/blukit

# Command or absolute path for the Claude Code CLI.
claude_bin = "claude"

# Scope used when you accept the prompt without typing anything.
default_scope = "127.0.0.1/32"

# How many .mcp.json backups to keep beside a project (0 disables).
backup_keep = 5

# Set false to launch without touching .mcp.json at all.
write_mcp_json = true

# Record every launch in a local append-only log.
audit = true

# Where that log lives (empty: $XDG_STATE_HOME/blukit/launches.jsonl).
audit_path = ""
```

| Key | Default | Effect |
|---|---|---|
| `claude_bin` | `"claude"` | Command or absolute path for the Claude Code CLI. A value containing a path separator is expanded with `~` handling and used as-is; a bare name is resolved on `PATH`. |
| `default_scope` | `"127.0.0.1/32"` | The scope used when you accept the prompt without typing anything, when `-y` is given, or when stdin is not a TTY. Normalised on load; a value the scope parser rejects is ignored with a warning and the loopback default is used instead. |
| `backup_keep` | `5` | How many timestamped `.mcp.json.*.bak` files to keep beside a project. A whole number: `true`, `1.9` and `"5"` are rejected with a warning and the default is used. `0` disables backups entirely; negative values are rejected too. |
| `write_mcp_json` | `true` | Set `false` to launch without touching `.mcp.json` at all, as if `--no-write` had been passed. |
| `audit` | `true` | Set `false` to launch without appending to the launch log, as if `--no-audit` had been passed on every run. |
| `audit_path` | `""` | Where the launch log lives. Empty uses `$XDG_STATE_HOME/blukit/launches.jsonl`, which is `~/.local/state/blukit/launches.jsonl` by default; a leading `~` in a value you set is expanded. |
| `[extra_env]` | empty | A table of string values merged into the environment handed to the Claude Code CLI. It cannot set `DEFENSE_ALLOWED_TARGETS`; that entry is dropped and reported. |

A malformed file never stops a launch: every bad key falls back to its default and the run
continues. Each problem is reported as an `blukit: ...` warning on stderr — by `blukit run` before
it does anything else, and by `blukit config show` and `blukit doctor`. For example:

```console
$ blukit config show
blukit: unknown key 'unknown_key' ignored
blukit: claude_bin must be a non-empty string
blukit: default_scope rejected ('!!nonsense!!' contains characters that are not valid in a target); using 127.0.0.1/32
blukit: backup_keep must be an integer
blukit: write_mcp_json must be true or false
claude_bin        = claude
default_scope   = 127.0.0.1/32
backup_keep     = 5
write_mcp_json  = true
audit           = true
audit_path      = /home/you/.local/state/blukit/launches.jsonl (default)
config file     = /tmp/bad.toml
```

`blukit config init` does not write an `[extra_env]` table, because there is nothing to put in one.
Add it by hand:

```toml
[extra_env]
DEFENSE_ALLOW_PIVOT = "1"
```

Keys that TOML cannot express bare — anything with a space, a colon, or a dot — are quoted when the
config is written, and control characters are escaped, so an unusual key or value survives a save and
reload instead of corrupting the file.

`[extra_env]` cannot set the scope. An entry named `DEFENSE_ALLOWED_TARGETS` is dropped and reported
as a config problem, and the scope blukit exports is the one it recorded:

```console
$ blukit config show
blukit: extra_env cannot set DEFENSE_ALLOWED_TARGETS; the authorised scope is the one that is recorded
```

## Environment variables

| Variable | Effect |
|---|---|
| `BLUKIT_CLAUDE_BIN` | Absolute path or command for the Claude Code CLI. Overrides `claude_bin` in the config. |
| `BLUKIT_CONFIG` | Use this file as the config file instead of the default location. |
| `BLUKIT_CLAUDE_HOME` | Claude Code home directory used to locate `themes/`. Defaults to `~/.claude`. |
| `XDG_STATE_HOME` | Directory the default launch log lives under: the log is `<XDG_STATE_HOME>/blukit/launches.jsonl`. Defaults to `~/.local/state`. A configured `audit_path` takes precedence. |
| `DEFENSE_ALLOWED_TARGETS` | Read as the scope when `--scope` is absent and the value is non-empty. |

blukit also sets `CLAUDE_CODE_IDENTITY_NAME` and `CLAUDE_CODE_IDENTITY_SLUG` to `blukit` in the
environment it hands to the CLI, unless you have already set them yourself or overridden them
through `[extra_env]`.

## How the scope is resolved and written

The scope comes from the first of these that applies:

1. `--scope` on the command line.
2. A non-empty `DEFENSE_ALLOWED_TARGETS` in the environment.
3. The configured `default_scope`, when `-y` was passed or stdin is not a TTY.
4. An interactive prompt. An empty answer is the configured `default_scope`.

Scope is your call. blukit never substitutes a wider target of its own, never guesses a range, and
the only fallback it applies when it has nothing to go on is the loopback `127.0.0.1/32`. It will not
quietly widen what you typed either: a network whose host bits are set is refused rather than
rounded down.

That is how `run` decides what to enforce. To change what is recorded *without* launching anything,
use `blukit scope set`, `add` or `remove`, which write through the same validated, atomic,
backed-up path — see [`blukit scope`](#blukit-scope).

Whatever the source, the value is normalised before it is written:

| Input | Normalised |
|---|---|
| `10.0.0.5` | `10.0.0.5/32` |
| `10.0.0.0/24` | `10.0.0.0/24` |
| `APP.Example.COM.` | `app.example.com` |
| `solo`, `none`, `off`, empty | `127.0.0.1/32` |
| `2001:db8::1` | `2001:db8::1/128` |
| `2001:db8::/32` | `2001:db8::/32` |
| `https://app.lan:8443/api` | `app.lan` |
| `10.66.66.10:8080` | `10.66.66.10/32` |

Entries are comma-separated, IPv4 and IPv6 are both accepted, and duplicates are dropped. A target
may also be a webapp/API endpoint — a URL such as `https://app.lan:8443/api`, or a bare
`host:port` — which is reduced to its host, since the `defense` server enforces on host and IP; the
scheme, port, path and any credentials are dropped. A CIDR or netmask (`10.0.0.0/24`,
`10.0.0.0/255.255.255.0`) is always read as a network, never as an endpoint path. A token
longer than 253 characters, or one carrying a shell metacharacter or whitespace where a host or
network is expected, is rejected with `exit 2` rather than salvaged.

Four kinds of entry are refused rather than accepted or adjusted:

| Refused | Message |
|---|---|
| `10.0.0.5/24`, whose host bits are set | `'10.0.0.5/24' has host bits set; did you mean 10.0.0.0/24?` |
| `0.0.0.0/0` or `::/0`, without `--allow-any` | `'0.0.0.0/0' covers every address; pass --allow-any to confirm that is intended` |
| `*.example.com` — wildcards of any sort | `'*.example.com' uses wildcard matching, which the tool server cannot enforce` |
| `0x7f000001`, `0x7f.1` — legacy numeric forms | `'0x7f000001' is a legacy numeric address form that resolvers read as a different host; write it as a dotted-quad address instead` |

The wildcard refusal is deliberate. The companion tool server matches on literal addresses, so a
wildcard entry could never have matched anything; refusing it is better than recording a scope that
looks broader than it is.

Two of those are worth spelling out. Wildcards used to be accepted here and are not any more, so
replace `*.example.com` with the addresses you actually mean. And `curl`, `wget` and friends read the
legacy numeric forms as real addresses — `0x7f000001`, `0x7f.1` and `127.1` all reach `127.0.0.1` —
which would authorise a host nobody typed, so all of them are refused.

An address-shaped typo is caught too. A token made only of digits and dots has to parse as an IP
address or network, so an out-of-range octet fails clearly instead of being stored:

```console
$ blukit -s 10.0.0.256 -n
blukit: '10.0.0.256' looks like an IP address but is not valid
blukit: expected a comma-separated list of hosts, IPs, CIDRs or webapp/API URLs, or 'solo'
$ echo $?
2
```

### The deny list

`--deny` records a second value in the same `env` block, `DEFENSE_DENIED_TARGETS`. The tool server
checks the deny list first, so a deny entry wins over an allow entry. That is how you authorise a
whole subnet but refuse one host inside it:

```console
$ blukit -s 10.20.30.0/24 --deny 10.20.30.7 -n
blukit: would record 10.20.30.0/24 in /srv/engagements/acme/.mcp.json [deny: 10.20.30.7/32]
blukit: would launch /home/you/.claude/bin/claude
```

It is parsed and normalised by exactly the same rules as `--scope`, the range forms included, so
`--deny 10.20.30.10-20` expands just as it would as a scope, hostnames are lowercased, and an entry
that would be rejected as a scope is rejected here too — with `exit 2` and nothing written. The one
difference in parsing is the empty value, described below.

Two differences from `--scope` are deliberate:

- **Omitting `--deny` changes nothing.** Whatever deny list is recorded stays recorded, so
  `blukit -s 10.0.0.0/24` on a project that already denies a host goes on denying it. Only `--deny`
  replaces the list. The same holds for `scope set`, `add` and `remove`, which never touch it.
- **An empty `--deny` means "deny nothing", not the `solo` loopback alias.** The aliases (`solo`,
  `none`, `off`, empty) describe an *allow* scope, where they mean "lock down to loopback". Read the
  same way, `--deny ""` would have denied loopback itself, which is not what clearing a deny list
  means.

`blukit scope show` prints the deny list on a second line when one is recorded, and a `--dry-run`
names it beside the scope it would write. A change to the deny list alone is a real change: it writes
the file and takes a backup, exactly as a scope change does.

The write itself:

- The nearest `.mcp.json`, at or above the working directory, is used unless `--mcp-json` says
  otherwise.
- Two values can change: `mcpServers.defense.env.DEFENSE_ALLOWED_TARGETS`, and
  `DEFENSE_DENIED_TARGETS` when `--deny` is given. Every other key is read, re-serialised and written
  back unchanged. If the file declares no `defense` server, the run stops with `exit 1` rather than
  launching unscoped.
- A `<name>.lock` file beside the target is locked for the read-modify-write, so two blukit runs
  against the same project are serialised instead of racing. The lock file is created on first write
  and left in place.
- The new content is written to a staging file whose name is randomised (`.mcp.json.<random>.new`),
  parsed back, then swapped in with `os.replace`. The unpredictable name is the fix for a real
  vulnerability: with a fixed staging name, a hostile repository could have shipped a
  `.mcp.json.new` symlink and blukit would have written through it.
- The previous version is *copied* to `.mcp.json.<YYYYmmdd-HHMMSS>.bak` before the swap, so the
  target is never briefly missing. A further write in the same second takes a `-1`, `-2` … suffix
  rather than overwriting an existing backup.
- The target keeps its permissions. A `.mcp.json` set to `0600` stays `0600` across a write, rather
  than being reset to the umask on a file that may hold a `DEFENSE_TOKEN`.
- When both values already match what was asked for, nothing is written and no backup is created. A
  deny list that changed on its own is not that case: it is written and backed up like any other
  change.
- Backups beyond `backup_keep` are deleted, oldest first, ordered by their timestamp and then by
  that suffix, so the newest survive.
- Input that cannot be read as JSON is reported as a message, not a traceback: invalid UTF-8, invalid
  JSON, and nesting too deep to parse all produce a clean error.
- Under `--dry-run` every check above is evaluated against the same target and only the file mutation
  is skipped, so the answers match a real run. It does not create the staging file or take the lock,
  which is why it cannot report that the directory is unwritable.

Because the whole file is re-serialised, a first run reformats `.mcp.json` to two-space indentation.
Existing keys keep their order, but a missing `env` object is appended to the server entry, and any
formatting you had is not preserved.

## Theme

```console
$ blukit theme install
blukit: theme written to /home/you/.claude/themes/blukit.json
blukit: set theme = "blukit" in your tui.toml to use it
```

A theme file already in place is never overwritten by accident. If it matches the bundled theme the
command is a no-op that still reports success; if you have edited it, the difference is reported and
nothing is written:

```console
$ blukit theme install
blukit: /home/you/.claude/themes/blukit.json already exists and differs from the bundled theme
blukit: re-run with --force to overwrite it
$ echo $?
1
```

`blukit theme install --force` replaces the file, discarding your local edit.

The installer refuses to write through a symlink at the destination, whatever flags you pass. A theme
file that is really a link to somewhere else is a path someone else chose, and blukit will not follow
it:

```console
$ blukit theme install
blukit: /home/you/.claude/themes/blukit.json is a symlink; refusing to write through it
$ echo $?
1
```

Then set the theme in your Claude Code TUI config:

```toml
theme = "blukit"
```

blukit writes that line nowhere itself. The file is `~/.claude/tui.toml`, the TUI configuration
of the Claude Code CLI. `BLUKIT_CLAUDE_HOME` changes only where blukit looks for and writes the
`themes/` directory; it does not change where the CLI reads its own config, so set it only if you
have deliberately relocated the themes directory. `install.sh` copies the theme unconditionally, so
it will overwrite a local edit that `blukit theme install` would leave alone, and it has no symlink
check, so it will write through a link where the subcommand refuses to. That copy is also why
`blukit doctor` usually reports the theme as already installed.

## Limitations

- **The scope check is a typo guard, not a security boundary.** It rejects malformed input and
  normalises what is left. It cannot know what the tool server will accept, and it does not verify
  that the server honours the scope it is given. Enforcement is the tool server's job.
- **blukit cannot confirm the scope was honoured.** It writes one environment value into one file
  and execs the CLI. If your tool server reads its scope from somewhere else — a command-line flag,
  a database, its own config — writing `.mcp.json` will not change what it allows.
- **`--no-write` only exports the environment.** When you use it, the `env` block already in
  `.mcp.json` still wins for the MCP child process, so a stale value there stays in force. Use
  `--no-write` when the file already holds the scope you want, or when your tool server reads the
  process environment rather than the file.
- **A config problem is warned about, not fixed.** `blukit run` prints the same warnings
  `blukit config show` and `blukit doctor` do, and then launches with the default for every bad
  key — including `default_scope`, which falls back to the loopback `127.0.0.1/32`. Only an
  unrecordable *scope* stops a run; a bad config value does not.
- **`--allow-unscoped` is the one way past the fail-closed default, and it is not free.** The session
  starts with the scope the file already held, which may be wider than the one you asked for, and
  every tool call in it is governed by that. blukit prints the difference, but nothing else does.
- **`--allow-any` authorises every address.** `0.0.0.0/0` and `::/0` are refused without it precisely
  because accepting one is the largest scope there is.
- **`--dry-run` cannot see an unwritable destination.** It checks the scope and the target file, but
  it creates no staging file and takes no lock, so it cannot tell you the directory is read-only. A
  dry run can therefore report `would record` for a project where the real run then exits `1`.
- **`blukit doctor` exits `0` when no `.mcp.json` exists.** It reports the absence and then checks
  only the other items. Do not treat `doctor`'s exit code as proof that a project is scoped.
- **blukit does not install, configure, or authenticate the Claude Code CLI.** If the binary is not
  found it prints where it looked and exits `1`.
- **There is no Windows support.** The package targets Linux and macOS, `install.sh` is bash, and
  the launcher uses `exec`.
- **blukit cannot undo a scope it recorded.** Uninstalling leaves `.mcp.json` exactly as your last
  run left it, including the recorded scope. Run `blukit scope clear` first if that matters.
- **`blukit scope remove` subtracts entries, not address space.** Removing `10.20.40.5/32` from a
  record that also holds `10.20.40.0/24` takes out only the host entry, so the `/24` still authorises
  it. Overlapping entries are not collapsed either, and nothing checks whether one entry contains
  another.
- **`blukit watch` watches the record, not the running session.** It samples `.mcp.json` and
  reports what it finds; it cannot see which scope a tool server process has already loaded, and it
  stops nothing. A widening it catches is a warning that a session started from that moment would be
  wider than you authorised — not a claim about what an in-flight process is still doing.
- **There is no verification of `.mcp.json` beyond JSON validity.** A file that is valid JSON but
  structurally wrong for your tool server is written as-is.
- **blukit does not vet the project you run it in.** It rewrites the `.mcp.json` it finds, and the
  Claude Code CLI executes that file's `command`. See the warning below.
- **The launch log is a local file that belongs to you, and it is not tamper-proof.** It is appended
  to `$XDG_STATE_HOME/blukit/launches.jsonl` (`~/.local/state/blukit/launches.jsonl` by default)
  with mode `0600` in a `0700` directory, and whoever ran the launcher can edit or delete it. Treat
  it as a personal record, not as evidence about a third party.
- **The log records what was asked for, not what was permitted.** `enforced` says only that blukit
  wrote that value into the `.mcp.json` named on the same line. It does not prove the tool server
  honoured the scope, and nothing in the file shows what the session then did. Nothing reads the log
  to make a decision.
- **A launch is not always logged.** A log that cannot be written never stops a launch: blukit
  prints `blukit: could not record this launch: ...` on stderr and carries on, so an unlogged launch
  is possible and that warning is the only signal.
- **The log is a record of launches, not of intent.** A dry run, a scope the parser rejected, and a
  run that could not find the Claude Code CLI all log nothing — the last of those after the scope has
  already been written into `.mcp.json`.
- **The log is never rotated.** Nothing prunes it and there is no size cap, so it grows by one line
  per launch until you shorten or delete it yourself.

## Tests

`python3 -m unittest discover -s tests -t .` from the project root runs the suite: 343 tests,
covering the scope parser, the `.mcp.json` writer, the config loader, the theme installer, the
launch log and the CLI. It passes. Run it before trusting a scripted pipeline to a change:

```console
$ python3 -m unittest discover -s tests -t .
[... one line of dots, one per test ...]
----------------------------------------------------------------------
Ran 343 tests in 7.263s

OK
```

## Do not commit `.mcp.json`

`.mcp.json` contains the scope you are authorised to test — often a client's address range — and it
may contain a tool-server token. Leave it out of version control in your own projects. blukit writes
its backups, its staging file and its lock file beside the target, so every pattern has to cover any
`*.mcp.json` basename and not just the default `.mcp.json`. The `.gitignore` in this repository is a
reasonable starting point:

```gitignore
.mcp.json
*.mcp.json
.mcp.json.*.bak
*.mcp.json.*.bak
.mcp.json.new
*.mcp.json.new
.mcp.json.*.new
*.mcp.json.*.new
.mcp.json.lock
*.mcp.json.lock
```

### A project's `.mcp.json` is code you did not write

blukit rewrites whichever `.mcp.json` it finds by walking up from the working directory. It does not
ask who owns the file, and it does not check whether you trust the repository the file came with. And
blukit is not what runs that file: the Claude Code CLI executes the `command` in the `defense` server
entry when it starts the MCP server.

So a cloned repository that ships a `.mcp.json` gets two things as soon as you run blukit inside it,
before you have read any of it:

- that file's `command` is executed by the Claude Code CLI, as you, with your credentials within reach;
- that file's scope is rewritten to whatever you passed on the command line.

Every MCP client behaves this way — this is the model rather than a defect in blukit — but "clone the
repository and run an agent in it" is the workflow that walks straight into it. Read the `.mcp.json` of
any project you did not write before running blukit there, and look at what the `command` and `args`
point at:

```json
      "command": "python3",
      "args": ["tools_server.py"],
```

A `command` that is an absolute path, a shell, a downloader, or a script you have never seen is a
reason to stop. If you are not sure about the file, leave it alone: run blukit somewhere else, or
point `--mcp-json` at a file you wrote yourself.

## Licensing and pricing

blukit is proprietary, commercial, source-available software licensed under the
[BluKit Commercial License](LICENSE). It is licensed, not sold.

In short, from the licence text:

- **Per seat.** One seat is one individual who installs or operates the software. A seat may be
  reassigned only if the original holder stops using it entirely. The number of seats and the term
  are those stated on your order or receipt.
- **No redistribution.** You may not copy, publish, distribute, sublicense, rent, lease, lend, sell
  or otherwise make blukit available to any third party.
- **No hosted or service-bureau use.** You may not make blukit available as part of a hosted,
  managed, or service-bureau offering to third parties, whether or not you charge for it.
- **No derivatives.** You may not modify, adapt, translate, or create derivative works, except where
  that restriction is prohibited by applicable law.
- **Notices stay put.** You may not remove or alter copyright, licence, or attribution notices.
- **Source availability is for inspection only.** The source is provided so you can read, audit, and
  security-review it, not to fork it.
- **No warranty, and liability capped** at what you paid in the twelve months before a claim.
- **It terminates automatically** if you breach the terms, and you must then stop using and delete
  all copies.

See [`docs/pricing.md`](docs/pricing.md) for how the product is sold, and [`LICENSE`](LICENSE) for
the binding text. For anything other than the standard terms, contact the copyright holder.

## Uninstall

```bash
python3 -m pip uninstall blukit    # or: pipx uninstall blukit / uv tool uninstall blukit
rm -rf ~/.local/share/blukit       # the virtual environment install.sh may have used
rm -f ~/.local/bin/blukit          # the console script, or the link to it
rm -f ~/.claude/themes/blukit.json
rm -rf ~/.config/blukit
```

Then remove `theme = "blukit"` from `~/.claude/tui.toml`, or set it to another theme. The
timestamped `.mcp.json.*.bak` files and the `.mcp.json.lock` sidecar in your projects are yours to
clean up, and the scope last written into `.mcp.json` stays there until you change it —
`blukit scope clear` before uninstalling if you want it reset to `127.0.0.1/32`.

## Status

Version 0.1.0 — the first blue-team release, mirroring the shipped
[evilkit](https://github.com/Bowie1969/evilkit) v1.1.0 module for module. The scope parser, the
`.mcp.json` writer, the config, the theme installer, the launch log, `scope set/add/remove`, `watch`
and the mandatory model pin are the whole product: there is no plugin system and no Windows support.
The `defense` tool server the `.mcp.json` points at — a read-only telemetry query surface plus
scope-gated response actions — is a documented seam at this version: the `.mcp.json` wiring and the
`DEFENSE_ALLOWED_TARGETS` scope env var are real and covered by tests, and you supply the server. It
fails closed by default, and the [Limitations](#limitations) above are the honest list of what it
does not do.

## Disclaimer

Use blukit only against systems you are authorised to test. You are responsible for having written
permission for every target you put in a scope, and for complying with the terms of service of the
Claude Code CLI and any other third-party software involved. blukit is not affiliated with, endorsed
by, or produced by Anthropic or the Claude Code CLI. The software is provided as is, without
warranty, and the licensor's liability is limited as set out in the [licence](LICENSE).
