"""Dedicated, renewable ConnectLife session for Food Manager.

The session is deliberately separate from the ConnectLife HA integration.  It
keeps access and refresh tokens in memory only; the config entry contains just
the account credentials entered by the owner in the HA UI.
"""
from __future__ import annotations

from typing import Any


class FoodManagerAuthenticationError(RuntimeError):
    """The Food Manager account could not be authenticated."""


class FoodManagerConnectionError(RuntimeError):
    """Temporary transport/provider/protocol failure; credentials not disproved."""


def _login_error(err: Exception, message: str) -> RuntimeError:
    from ._vendor.connectlife.api import LifeConnectAuthError
    # A 429/5xx is never evidence of invalid credentials. Unknown provider or
    # decoding failures also retain HA's retry path instead of requesting reauth.
    status = getattr(err, "status", None)
    if isinstance(err, LifeConnectAuthError) and status in (None, 400, 401, 403):
        return FoodManagerAuthenticationError(message)
    return FoodManagerConnectionError(message)


def create_connectlife_api(username: str, password: str) -> Any:
    """Create a private EU gateway client, independent of other integrations."""
    from ._vendor.connectlife.api import ConnectLifeApi

    return ConnectLifeApi(username, password)


class FoodManagerAuthSession:
    """Keep one auto-renewing OAuth client for a Food Manager config entry."""

    def __init__(self, username: str, password: str) -> None:
        self._api = create_connectlife_api(username, password)

    async def async_login(self) -> None:
        """Verify credentials and obtain the first access/refresh token pair."""
        try:
            await self._api.login()
        except Exception as err:  # library versions expose different auth errors
            raise _login_error(err, "ConnectLife login failed") from err

    async def async_api(self) -> Any:
        """Return a client with a valid access token.

        ``_fetch_access_token`` is the client's token lifecycle method: it
        refreshes an expired access token and falls back to a complete login
        when its refresh token is no longer usable.
        """
        try:
            await self._api._fetch_access_token()  # noqa: SLF001 - no public equivalent
            return self._api
        except Exception as err:  # library versions expose different auth errors
            raise _login_error(err, "ConnectLife session renewal failed") from err
