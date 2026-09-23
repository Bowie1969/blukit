"""Read and update the ``defense`` server scope inside a project's ``.mcp.json``.

Two env keys belong to that scope: ``DEFENSE_ALLOWED_TARGETS`` and
``DEFENSE_DENIED_TARGETS``. The tool server checks the deny list first, so a
target can be inside the allowed scope and still be refused.

The write is atomic: a candidate file is built and parsed before it replaces
anything, and the previous version is kept as a timestamped backup. A project
must never be left with a half-written ``.mcp.json``, nor briefly with none.

Why the file and not just the environment: the ``env`` block in ``.mcp.json`` is
injected into the MCP child process and wins over the parent environment, so an
exported ``DEFENSE_ALLOWED_TARGETS`` does not reach the tool server.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import glob
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

__all__ = [
    "McpError",
    "McpNotFound",
    "McpExists",
    "NoDefenseServer",
    "SCOPE_ENV_KEY",
    "DENY_ENV_KEY",
    "RUN_ENV_KEY",
    "SERVER_NAME",
    "find",
    "read_scope",
    "read_deny",
    "write_scope",
    "scaffold",
    "build_defense_mcp",
    "prune_backups",
    "looks_like_backup",
]

SCOPE_ENV_KEY = "DEFENSE_ALLOWED_TARGETS"
DENY_ENV_KEY = "DEFENSE_DENIED_TARGETS"
RUN_ENV_KEY = "DEFENSE_RUN_DIR"
SERVER_NAME = "defense"

#: The default path recorded for the defense tool server when a project is
#: scaffolded. The server itself ships with the lab image (as evilkit's tool
#: server ships with its image), not with this launcher, so this is only a
#: pointer; override it with ``--defense-server`` or ``BLUKIT_DEFENSE_SERVER``.
DEFAULT_DEFENSE_SERVER = "defense_server.py"

_BACKUP_RE = re.compile(r"\.(?P<stamp>\d{8}-\d{6})(?:-(?P<suffix>\d+))?\.bak$")


class McpError(Exception):
    """Base class for .mcp.json handling failures."""


class McpNotFound(McpError):
    """No .mcp.json exists at the requested location."""


class McpExists(McpError):
    """A .mcp.json already exists where a fresh one was to be scaffolded."""


class NoDefenseServer(McpError):
    """The .mcp.json exists but declares no defense server."""


def find(start: Path | None = None) -> Path:
    """Locate .mcp.json at or above ``start`` (default: the working directory)."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".mcp.json"
        if candidate.is_file():
            return candidate
    raise McpNotFound(f"no .mcp.json found in {current} or any parent directory")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise McpError(f"could not read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise McpError(f"{path} is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise McpError(f"{path} is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise McpError(f"{path} is nested too deeply to parse") from exc
    if not isinstance(data, dict):
        raise McpError(f"{path} does not contain a JSON object")
    return data


def _defense_env(path: Path) -> dict | None:
    """The defense server's env block, or None when there is none to read."""
    data = _load(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    server = servers.get(SERVER_NAME)
    if not isinstance(server, dict):
        return None
    env = server.get("env")
    if not isinstance(env, dict):
        return None
    return env


def _read_env_key(path: Path, key: str) -> str | None:
    """Return ``key`` from the defense env block, or None when it is unset."""
    env = _defense_env(path)
    if env is None:
        return None
    value = env.get(key)
    return value if isinstance(value, str) else None


def read_scope(path: Path) -> str | None:
    """Return the configured scope, or None when no defense server is declared."""
    return _read_env_key(path, SCOPE_ENV_KEY)


def read_deny(path: Path) -> str | None:
    """Return the configured deny list, or None when none is recorded.

    An empty string is a recorded answer — "deny nothing" — and is returned as
    such rather than folded into None, which means the key is not there at all.
    """
    return _read_env_key(path, DENY_ENV_KEY)


def looks_like_backup(path: Path) -> bool:
    return bool(_BACKUP_RE.search(path.name))


def _backup_age(path: Path) -> tuple[str, int, str]:
    """Order backups oldest-first, including several written in one second."""
    match = _BACKUP_RE.search(path.name)
    if match is None:
        return ("", 0, path.name)
    return (match["stamp"], int(match["suffix"] or 0), path.name)


def _free_backup_path(path: Path) -> Path:
    """Pick an unused timestamped backup name beside ``path``."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.{stamp}.bak")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.{stamp}-{suffix}.bak")
        suffix += 1
    return candidate


def prune_backups(path: Path, keep: int) -> list[Path]:
    """Delete the oldest backups beside ``path``, keeping the newest ``keep``."""
    if keep < 0 or not path.parent.is_dir():
        return []
    prefix = path.name + "."
    backups = sorted(
        (
            candidate
            for candidate in path.parent.glob(f"{glob.escape(path.name)}.*.bak")
            if candidate.name.startswith(prefix) and looks_like_backup(candidate)
        ),
        key=_backup_age,
    )
    stale = backups[: max(0, len(backups) - keep)]
    removed: list[Path] = []
    for old in stale:
        try:
            old.unlink()
        except OSError:
            continue
        removed.append(old)
    return removed


def _discard(staging: Path) -> None:
    try:
        staging.unlink()
    except OSError:
        pass


def _original_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _stage(path: Path, payload: str) -> Path:
    """Write ``payload`` to a fresh staging file beside ``path``.

    ``mkstemp`` creates the file with ``O_CREAT|O_EXCL`` under a name that
    cannot be predicted, so no symlink can be planted at the staging path.
    """
    mode = _original_mode(path)
    try:
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".new")
    except OSError as exc:
        raise McpError(f"could not stage a new {path.name}: {exc}") from exc

    staging = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if mode is not None:
                os.fchmod(fd, mode)
            handle.write(payload)
    except OSError as exc:
        _discard(staging)
        raise McpError(f"could not stage a new {path.name}: {exc}") from exc
    return staging


def _revalidate(staging: Path, path: Path) -> None:
    try:
        json.loads(staging.read_text(encoding="utf-8"))
    except OSError as exc:
        raise McpError(f"could not read back the staged {path.name}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpError(f"refusing to write invalid JSON to {path.name}: {exc}") from exc
    except RecursionError as exc:
        raise McpError(f"refusing to write deeply nested JSON to {path.name}") from exc


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Serialise read-modify-write cycles with an advisory lock beside ``path``."""
    lock = path.with_name(path.name + ".lock")
    try:
        handle = open(lock, "a+b")
    except OSError as exc:
        raise McpError(f"could not open {lock}: {exc}") from exc
    with handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise McpError(f"could not lock {lock}: {exc}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_scope(
    path: Path,
    scope: str,
    keep_backups: int = 5,
    dry_run: bool = False,
    deny: str | None = None,
) -> bool:
    """Record ``scope``, and ``deny`` when it is given, in ``path``.

    ``deny`` is the denied-target list. ``None`` means the caller said nothing
    about it, so whatever is recorded stays untouched — an omission must never
    blank a deny list someone set earlier. A string is recorded as the new
    value; ``""`` is the explicit "no denied targets".

    Returns True when the file changed, False when it already held that scope
    and that deny list (in which case nothing is written and no backup is
    created). A deny-only change is a change: it writes and backs up like any
    other. With ``dry_run`` the checks run and the answer is the same, but no
    file is touched.
    """
    if dry_run:
        return _rewrite(path, scope, deny, keep_backups, dry_run=True)
    with _locked(path):
        return _rewrite(path, scope, deny, keep_backups, dry_run=False)


def build_defense_mcp(
    scope: str,
    *,
    defense_server: str = DEFAULT_DEFENSE_SERVER,
    run_dir: str = "",
    deny: str = "",
) -> dict:
    """Return a minimal ``.mcp.json`` object wiring the defense server to ``scope``.

    This is the whole file an engagement needs: one ``defense`` server, started
    with the recorded scope in its env block, and every optional capability off.
    ``run_dir`` is the telemetry directory the server reads (``DEFENSE_RUN_DIR``);
    it is left blank when not supplied, which the server treats as "nothing to
    observe yet" rather than a default.
    """
    return {
        "mcpServers": {
            SERVER_NAME: {
                "command": "python3",
                "args": [defense_server],
                "env": {
                    SCOPE_ENV_KEY: scope,
                    DENY_ENV_KEY: deny,
                    RUN_ENV_KEY: run_dir,
                    "DEFENSE_ALLOW_RESPONSE": "0",
                    "DEFENSE_ALLOW_INTERPRETER": "0",
                    "DEFENSE_ALLOW_ANY_CMD": "0",
                    "DEFENSE_TOKEN": "",
                },
            }
        }
    }


def scaffold(
    path: Path,
    scope: str,
    *,
    defense_server: str = DEFAULT_DEFENSE_SERVER,
    run_dir: str = "",
    deny: str = "",
    keep_backups: int = 5,
    force: bool = False,
) -> Path:
    """Write a fresh ``.mcp.json`` at ``path`` wiring the defense server to ``scope``.

    This is what lets an engagement start from nothing but the target: there is
    no file to hand-build first. It fails closed on an existing file — raising
    :class:`McpExists` unless ``force`` — so it can never quietly overwrite a
    project's real configuration; with ``force`` the old file is backed up first,
    exactly as :func:`write_scope` backs up before a change. The write is atomic
    and revalidated, so a project is never left with half a file or none.
    """
    with _locked(path):
        exists = path.exists()
        if exists and not force:
            raise McpExists(f"{path} already exists; not overwriting without force")

        data = build_defense_mcp(
            scope, defense_server=defense_server, run_dir=run_dir, deny=deny
        )
        payload = json.dumps(data, indent=2) + "\n"
        staging = _stage(path, payload)
        try:
            _revalidate(staging, path)
            if exists and keep_backups > 0:
                backup = _free_backup_path(path)
                try:
                    shutil.copy2(path, backup)
                except OSError as exc:
                    raise McpError(f"could not back up {path}: {exc}") from exc
            try:
                os.replace(staging, path)
            except OSError as exc:
                raise McpError(f"could not write {path}: {exc}") from exc
        except BaseException:
            _discard(staging)
            raise

    if exists and keep_backups > 0:
        prune_backups(path, keep_backups)
    return path


def _rewrite(
    path: Path, scope: str, deny: str | None, keep_backups: int, *, dry_run: bool
) -> bool:
    data = _load(path)

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise McpError(f"{path}: 'mcpServers' is not an object")
    server = servers.get(SERVER_NAME)
    if not isinstance(server, dict):
        raise NoDefenseServer(f"{path} declares no '{SERVER_NAME}' server")

    env = server.setdefault("env", {})
    if not isinstance(env, dict):
        raise McpError(f"{path}: '{SERVER_NAME}.env' is not an object")

    # Every key that differs, so "nothing to do" is a judgement about all of
    # them: a deny list that moved on its own still has to be written.
    changes: dict[str, str] = {}
    if env.get(SCOPE_ENV_KEY) != scope:
        changes[SCOPE_ENV_KEY] = scope
    if deny is not None and env.get(DENY_ENV_KEY) != deny:
        changes[DENY_ENV_KEY] = deny
    if not changes:
        return False
    env.update(changes)

    if dry_run:
        return True

    try:
        payload = json.dumps(data, indent=2) + "\n"
    except RecursionError as exc:
        raise McpError(f"{path} is nested too deeply to rewrite") from exc

    staging = _stage(path, payload)

    try:
        _revalidate(staging, path)

        if keep_backups > 0:
            backup = _free_backup_path(path)
            try:
                shutil.copy2(path, backup)
            except OSError as exc:
                raise McpError(f"could not back up {path}: {exc}") from exc

        try:
            os.replace(staging, path)
        except OSError as exc:
            # The original was copied, never moved, so it is untouched by this.
            raise McpError(f"could not replace {path}: {exc}") from exc
    except BaseException:
        _discard(staging)
        raise

    if keep_backups > 0:
        prune_backups(path, keep_backups)
    return True
