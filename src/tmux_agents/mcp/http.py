"""MCP Streamable HTTP transport entrypoint.

Defaults to localhost binding, DNS rebinding protection, and optional
bearer token authentication via TMUX_AGENTS_AUTH_TOKEN env var.
Safe mode (default) omits destructive tools.
"""

from __future__ import annotations

from tmux_agents.logging import get_logger


def run_http_server(
    host: str = "127.0.0.1",
    port: int = 8766,
    *,
    safe_mode: bool = True,
    auth_token: str | None = None,
) -> None:
    """Launch the MCP server over Streamable HTTP transport."""
    log = get_logger("mcp.http")
    log.info("initializing_http_transport", host=host, port=port, safe_mode=safe_mode)

    from mcp.server.transport_security import TransportSecuritySettings

    from tmux_agents.mcp.auth import BearerTokenVerifier
    from tmux_agents.mcp.server_common import create_server

    server = create_server(safe_mode=safe_mode)

    # Configure transport security
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{host}:{port}", host, f"localhost:{port}", "localhost"],
    )
    server.settings.transport_security = security

    # Configure bearer auth if token is available
    verifier = BearerTokenVerifier(expected_token=auth_token)
    if verifier.is_configured:
        server.settings.token_verifier = verifier  # type: ignore[attr-defined]
        log.info("bearer_auth_enabled")
    else:
        log.info("bearer_auth_disabled", hint="Set TMUX_AGENTS_AUTH_TOKEN to enable")

    # Override host/port on server settings
    server.settings.host = host
    server.settings.port = port

    log.info("http_server_ready", host=host, port=port)
    server.run(transport="streamable-http")
