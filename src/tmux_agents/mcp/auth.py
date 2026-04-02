"""Authentication support for MCP HTTP transport.

Provides a static bearer token verifier for the MCP SDK's TokenVerifier
protocol. The token is read from TMUX_AGENTS_AUTH_TOKEN environment variable
or passed explicitly.
"""

from __future__ import annotations

import os

from mcp.server.auth.provider import AccessToken

from tmux_agents.logging import get_logger

log = get_logger(__name__)

ENV_TOKEN_VAR = "TMUX_AGENTS_AUTH_TOKEN"


class BearerTokenVerifier:
    """Static bearer token verifier for MCP HTTP transport.

    Implements the MCP SDK TokenVerifier protocol: verify_token(token) -> AccessToken | None.
    """

    def __init__(self, expected_token: str | None = None) -> None:
        self._token = expected_token or os.environ.get(ENV_TOKEN_VAR)

    @property
    def is_configured(self) -> bool:
        return self._token is not None and len(self._token) > 0

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._token:
            # No token configured — allow all requests
            return AccessToken(token=token, client_id="anonymous", scopes=[], expires_at=None)

        if token == self._token:
            return AccessToken(token=token, client_id="bearer", scopes=["full"], expires_at=None)

        log.warning("auth_token_rejected")
        return None
