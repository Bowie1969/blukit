"""MCP stdio server loop. Line-delimited JSON-RPC 2.0.

Handshake: ``initialize`` (reply with ``protocolVersion`` echoed),
``notifications/initialized`` (silently acknowledged), ``tools/list``,
``tools/call``. ``ping`` is answered with an empty result; any other method
with an id gets ``-32601 method not found``. A malformed line produces
``-32700 parse error`` with ``id: null``.

The loop runs until stdin closes. State (scope, backend, engagement) is
captured once at startup from the environment — blukit guarantees the
``.mcp.json`` env block is already in place by the time the server starts.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

from . import __version__
from . import backends as backends_mod
from . import scope as scope_mod
from . import tools as tools_mod
from .engagement import from_env as engagement_from_env

__all__ = ["serve", "PROTOCOL_VERSION"]

PROTOCOL_VERSION = "2024-11-05"


def _reply(id_: Any, result: Any = None, error: dict | None = None) -> str:
    msg: dict = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return json.dumps(msg)


def _handle(msg: dict, state: dict) -> str | None:
    method = msg.get("method")
    id_ = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _reply(id_, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "defense", "version": __version__},
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _reply(id_, {
            "tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                for t in tools_mod.all_tools()
            ]
        })

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result = tools_mod.dispatch(
                name, arguments, state["scope"], state["backend"], state["engagement"],
            )
            return _reply(id_, result)
        except Exception as exc:  # noqa: BLE001 — surface anything else as JSON-RPC error
            return _reply(id_, error={"code": -32000, "message": f"{type(exc).__name__}: {exc}"})

    if method == "ping":
        return _reply(id_, {})

    if id_ is None:
        return None  # unknown notification — drop

    return _reply(id_, error={"code": -32601, "message": f"method not found: {method}"})


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None,
          env: dict | None = None) -> int:
    env = os.environ if env is None else env
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    state = {
        "scope": scope_mod.load(env),
        "backend": backends_mod.select(env),
        "engagement": engagement_from_env(env),
    }
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(_reply(None, error={"code": -32700, "message": "parse error"}) + "\n")
            stdout.flush()
            continue
        reply = _handle(msg, state)
        if reply is not None:
            stdout.write(reply + "\n")
            stdout.flush()
    return 0
