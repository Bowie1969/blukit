"""Engagement log — append-only, written to ``DEFENSE_RUN_DIR``.

Every tool call appends one JSON line to ``events.jsonl`` and one bullet
to ``log.md``. The two files are complementary: ``events.jsonl`` is
machine-readable (feeds P3's ``finalise_engagement`` later); ``log.md`` is
what a human reads tomorrow morning.

Append order is preserved under concurrent writers via a POSIX advisory
lock. The lock is held only for the write — no coordination beyond that.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
from pathlib import Path

__all__ = ["Engagement", "from_env"]


class Engagement:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "log.md"
        self.events_path = self.run_dir / "events.jsonl"

    def append(self, action: str, data: dict | None = None) -> None:
        data = data or {}
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        line = {"ts": ts, "action": action, **data}
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(line, sort_keys=True) + "\n")
            fcntl.flock(fh, fcntl.LOCK_UN)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(f"- `{ts}` **{action}** — {_summary(data)}\n")
            fcntl.flock(fh, fcntl.LOCK_UN)


def _summary(data: dict) -> str:
    parts = []
    for k, v in data.items():
        if k == "preview":
            continue
        s = str(v).replace("\n", " ")
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return "; ".join(parts) or "(no data)"


def from_env(env: dict | None = None) -> Engagement:
    env = os.environ if env is None else env
    run_dir = env.get("DEFENSE_RUN_DIR", "").strip()
    if not run_dir:
        run_dir = str(Path.home() / ".local" / "state" / "defense" /
                      _dt.date.today().isoformat())
    return Engagement(Path(run_dir))
