"""Defence MCP tool server for blukit-launched sessions.

Blukit records the authorized scope into ``.mcp.json``'s ``defense`` server
``env`` block, then execs the Claude Code CLI. This package *is* that
``defense`` server: an MCP stdio server with the tool surface a blue
operator needs during an active engagement — logs, process state, network
state, pcap capture, host isolation, artifact collection, and an
engagement log.

Every tool is scope-gated against ``DEFENSE_ALLOWED_TARGETS`` and
``DEFENSE_DENIED_TARGETS`` the same way blukit's launcher validates the
input it writes. The server trusts no target it has not been told about.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
