"""OAuth accounts: signing in, keeping the token, and renewing it.

Signing in follows the specs from the JMAP server outwards: an unauthenticated
request draws a Bearer challenge naming the protected-resource metadata (RFC
9728), which names the authorization server (RFC 8414). With no client id
configured, the command registers itself as a public client (RFC 7591), then runs
the authorization-code flow with PKCE through a loopback redirect, or the device
flow (RFC 8628) where no browser can reach this machine.

The token lives in the keyring as JSON. ``OAuth2Auth`` renews it shortly before
it expires and saves each new one through :class:`OAuthTokenStore` before the old
refresh token is discarded, which matters for servers that rotate them.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from jmap.auth import (
    OAuth2Auth,
    OAuth2Token,
    OAuthClient,
    find_challenge,
    loopback_receiver,
    parse_challenges,
    protected_resource_url,
)
from jmap.auth.flows import parse_redirect
from jmap.auth.metadata import DEVICE_CODE_GRANT
from jmap.cli.config import OAuthSettings
from jmap.cli.connect import session_url
from jmap.cli.errors import AuthError
from jmap.discovery import domain_of, well_known_url

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from jmap.auth.metadata import AuthorizationServerMetadata
    from jmap.cli.config import ResolvedAccount
    from jmap.cli.secrets import SecretStore

#: The name the command registers under.
CLIENT_NAME: Final = "jmaplib"

#: How long to wait for the browser to come back, in seconds.
REDIRECT_TIMEOUT: Final = 300.0


class OAuthTokenStore:
    """One account's token in the secret store, as ``dataclasses.asdict`` JSON."""

    def __init__(self, secrets: SecretStore, account: str) -> None:
        self._secrets = secrets
        self._account = account

    def save(self, token: OAuth2Token) -> None:
        self._secrets.set(self._account, "oauth", dumps_token(token))

    def load(self) -> OAuth2Token | None:
        stored = self._secrets.get(self._account, "oauth")
        if not stored:
            return None
        try:
            return OAuth2Token(**json.loads(stored))
        except (ValueError, TypeError, ValidationError):
            return None  # unreadable is the same as absent: sign in again


def dumps_token(token: OAuth2Token) -> str:
    """``token`` as the JSON the keyring keeps; ``OAuth2Token(**json.loads(...))`` reads it."""
    return json.dumps(dataclasses.asdict(token))


def oauth_auth(
    account: ResolvedAccount,
    secrets: SecretStore,
    http: Callable[[], httpx.Client],
) -> OAuth2Auth:
    """The credential for an ``oauth`` account, renewing itself through ``http``."""
    store = OAuthTokenStore(secrets, account.name)
    token = store.load()
    if token is None:
        raise AuthError(
            f"account {account.name!r} has no OAuth token",
            hint=f"sign in with 'jmap account login {account.name}'",
        )
    return OAuth2Auth(token, refresh=Refresher(account, http), store=store)


class Refresher:
    """Renews a token at the account's authorization server."""

    def __init__(self, account: ResolvedAccount, http: Callable[[], httpx.Client]) -> None:
        self._account = account
        self._http = http

    def __call__(self, token: OAuth2Token) -> OAuth2Token:
        settings = self._account.oauth
        if not token.refresh_token or not settings.issuer or not settings.client_id:
            raise AuthError(
                "the OAuth token has expired and cannot be renewed",
                hint=f"sign in again with 'jmap account login {self._account.name}'",
            )
        with self._http() as http:
            metadata = OAuthClient.discover_from_issuer(settings.issuer, http=http)
            client = OAuthClient(metadata, client_id=settings.client_id, http=http)
            return client.refresh(token.refresh_token, scope=settings.scope or None)


@dataclass(frozen=True, slots=True)
class SignIn:
    token: OAuth2Token
    #: What renewing needs: the client id (registered if there was none) and issuer.
    settings: OAuthSettings


def sign_in(
    account: ResolvedAccount,
    *,
    http: httpx.Client,
    say: Callable[[str], None],
    device: bool = False,
    open_url: Callable[[str], object] | None = None,
    timeout: float = REDIRECT_TIMEOUT,
) -> SignIn:
    """Get a token for ``account``, by browser redirect or device code."""
    settings = account.oauth
    scope = settings.scope or None
    metadata = _metadata(account, http)
    client = OAuthClient(metadata, client_id=settings.client_id, http=http)
    if device:
        token = _device(client, scope, say, register=not settings.client_id)
    else:
        token = _browser(client, scope, say, open_url, timeout, register=not settings.client_id)
    issuer = metadata.issuer or settings.issuer
    return SignIn(
        token, OAuthSettings(client_id=client.client_id, scope=settings.scope, issuer=issuer)
    )


def _metadata(account: ResolvedAccount, http: httpx.Client) -> AuthorizationServerMetadata:
    """The authorization server, from the settings or from the JMAP server's challenge."""
    if account.oauth.issuer:
        return OAuthClient.discover_from_issuer(account.oauth.issuer, http=http)
    start = session_url(account.url) if account.url else well_known_url(domain_of(account.address))
    response = http.get(start, headers={"Accept": "application/json"})
    resource = str(response.url)
    bearer = find_challenge(
        parse_challenges(response.headers.get_list("www-authenticate")), "bearer"
    )
    pointer = bearer.resource_metadata if bearer is not None else None
    return OAuthClient.discover(
        pointer or protected_resource_url(_origin(resource)), resource=resource, http=http
    )


def _browser(
    client: OAuthClient,
    scope: str | None,
    say: Callable[[str], None],
    open_url: Callable[[str], object] | None,
    timeout: float,
    *,
    register: bool,
) -> OAuth2Token:
    with loopback_receiver() as receiver:
        if register:
            client.register(CLIENT_NAME, redirect_uris=[receiver.redirect_uri], scope=scope)
        url, pkce, state = client.authorization_url(receiver.redirect_uri, scope=scope)
        receiver.expect_state(state)
        say(f"Open this address to sign in:\n{url}")
        if open_url is not None:
            open_url(url)
        code = parse_redirect(receiver.wait(timeout), expected_state=state)
        return client.exchange_code(
            code, redirect_uri=receiver.redirect_uri, verifier=pkce.verifier
        )


def _device(
    client: OAuthClient, scope: str | None, say: Callable[[str], None], *, register: bool
) -> OAuth2Token:
    if register:
        client.register(CLIENT_NAME, scope=scope, grant_types=(DEVICE_CODE_GRANT, "refresh_token"))
    flow = client.begin_device_flow(scope=scope)
    say(f"On any device, open {flow.verification_uri} and enter {flow.user_code}")
    return client.poll_device_flow(flow)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
