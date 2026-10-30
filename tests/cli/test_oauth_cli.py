"""OAuth accounts: signing in by browser and by device code, keeping and renewing the token."""

from __future__ import annotations

import json
import threading
import urllib.request
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from jmap.auth import OAuth2Token
from jmap.cli.app import run
from jmap.cli.config import (
    AccountConfig,
    Config,
    OAuthSettings,
    ResolvedAccount,
    load_config,
    save_config,
)
from jmap.cli.errors import AuthError
from jmap.cli.oauth import OAuthTokenStore, Refresher, dumps_token, oauth_auth, sign_in
from jmap.cli.paths import config_path
from jmap.cli.secrets import MemoryStore

from .conftest import URL

if TYPE_CHECKING:
    from jmap.testing import FakeJMAPServer

ISSUER = "https://auth.example.com"
METADATA: dict[str, Any] = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "registration_endpoint": f"{ISSUER}/register",
    "device_authorization_endpoint": f"{ISSUER}/device",
    "code_challenge_methods_supported": ["S256"],
    "grant_types_supported": [
        "authorization_code",
        "refresh_token",
        "urn:ietf:params:oauth:grant-type:device_code",
    ],
}
ACCOUNT = ResolvedAccount(
    name="work", address="", url=URL, auth="oauth", username="", account_id=""
)


class AuthServer:
    """The authorization server, and the JMAP server demanding a token."""

    def __init__(self, server: FakeJMAPServer, *, challenge: bool = True) -> None:
        self.forms: list[dict[str, str]] = []
        self.registered: list[dict[str, Any]] = []
        self.challenge = challenge
        self.tokens = 0
        server.intercept = self.route

    def route(self, request: httpx.Request) -> httpx.Response | None:
        url = request.url
        if url.host == "auth.example.com":
            return self._auth(request)
        if url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(
                200,
                json={"resource": "https://jmap.example.com", "authorization_servers": [ISSUER]},
            )
        unauthenticated = "authorization" not in request.headers
        if url.path.endswith("/jmap/session") and unauthenticated:
            header = 'Bearer resource_metadata="/.well-known/oauth-protected-resource"'
            return httpx.Response(
                401, headers={"WWW-Authenticate": header if self.challenge else "Basic"}
            )
        return None

    def _auth(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=METADATA)
        if path == "/register":
            self.registered.append(json.loads(request.content))
            return httpx.Response(201, json={"client_id": "registered"})
        if path == "/device":
            return httpx.Response(
                200,
                json={
                    "device_code": "dc",
                    "user_code": "WXYZ",
                    "verification_uri": f"{ISSUER}/device",
                    "interval": 1,
                },
            )
        form = dict(httpx.QueryParams(request.content.decode()))
        self.forms.append(form)
        self.tokens += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"at{self.tokens}",
                "token_type": "Bearer",
                "refresh_token": f"rt{self.tokens}",
                "expires_in": 3600,
            },
        )


def browse(url: str) -> None:
    """Play the browser: follow the redirect the authorization URL implies."""
    params = httpx.QueryParams(url.split("?", 1)[1])
    target = f"{params['redirect_uri']}?code=abc&state={params['state']}"
    threading.Thread(
        target=lambda: urllib.request.urlopen(target, timeout=5).read(), daemon=True
    ).start()


@pytest.fixture
def auth_server(server: FakeJMAPServer) -> AuthServer:
    return AuthServer(server)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


def http(server: FakeJMAPServer) -> httpx.Client:
    return httpx.Client(**server.client_kwargs())


class TestSignIn:
    def test_by_browser_registering_a_client(self, server, auth_server):
        said: list[str] = []
        signed = sign_in(ACCOUNT, http=http(server), say=said.append, open_url=browse, timeout=5)
        assert signed.token.access_token == "at1"
        assert signed.settings == OAuthSettings(client_id="registered", issuer=ISSUER)
        assert auth_server.registered[0]["redirect_uris"][0].startswith("http://127.0.0.1:")
        assert auth_server.forms[0]["code"] == "abc"
        assert said[0].startswith("Open this address to sign in:")

    def test_by_browser_with_a_known_client_and_no_browser(self, server, auth_server):
        account = replace(ACCOUNT, oauth=OAuthSettings(client_id="mine"))

        def say(text: str) -> None:
            browse(text.split("\n")[1])  # the person opens the printed address

        signed = sign_in(account, http=http(server), say=say, timeout=5)
        assert signed.settings.client_id == "mine"
        assert auth_server.registered == []

    def test_by_device_code_with_a_known_client(self, server, auth_server):
        account = replace(ACCOUNT, oauth=OAuthSettings(client_id="mine", scope="jmap"))
        said: list[str] = []
        signed = sign_in(account, http=http(server), say=said.append, device=True)
        assert signed.token.refresh_token == "rt1"
        assert signed.settings.client_id == "mine"
        assert auth_server.registered == []
        assert said == [f"On any device, open {ISSUER}/device and enter WXYZ"]

    def test_device_flow_registers_for_the_device_grant(self, server, auth_server):
        sign_in(ACCOUNT, http=http(server), say=lambda _: None, device=True)
        assert (
            "urn:ietf:params:oauth:grant-type:device_code"
            in auth_server.registered[0]["grant_types"]
        )

    def test_a_known_issuer_skips_the_challenge(self, server, auth_server):
        account = replace(ACCOUNT, oauth=OAuthSettings(issuer=ISSUER))
        sign_in(account, http=http(server), say=lambda _: None, device=True)
        assert not any(r for r in server.requests)

    def test_without_a_challenge_the_well_known_metadata(self, server):
        AuthServer(server, challenge=False)
        account = replace(ACCOUNT, url="", address="alice@jmap.example.com")
        signed = sign_in(account, http=http(server), say=lambda _: None, device=True)
        assert signed.settings.issuer == ISSUER


class TestTokens:
    def test_the_store_round_trips(self):
        store = OAuthTokenStore(MemoryStore(), "work")
        assert store.load() is None
        token = OAuth2Token("at", refresh_token="rt", expires_at=2e9, scope="s")
        store.save(token)
        loaded = store.load()
        assert loaded is not None
        assert (loaded.access_token, loaded.refresh_token, loaded.expires_at) == ("at", "rt", 2e9)

    @pytest.mark.parametrize("stored", ["{", '{"nope": 1}', '{"access_token": ""}'])
    def test_an_unreadable_token_is_none(self, stored):
        secrets = MemoryStore()
        secrets.set("work", "oauth", stored)
        assert OAuthTokenStore(secrets, "work").load() is None

    def test_no_token_means_sign_in(self):
        with pytest.raises(AuthError, match="has no OAuth token") as caught:
            oauth_auth(ACCOUNT, MemoryStore(), lambda: httpx.Client())
        assert caught.value.hint == "sign in with 'jmap account login work'"

    def test_renewing(self, server, auth_server):
        account = replace(ACCOUNT, oauth=OAuthSettings(client_id="cid", issuer=ISSUER))
        token = Refresher(account, lambda: http(server))(OAuth2Token("old", refresh_token="r0"))
        assert token.access_token == "at1"
        assert auth_server.forms[0] == {
            "grant_type": "refresh_token",
            "refresh_token": "r0",
            "client_id": "cid",
        }

    def test_renewing_needs_a_refresh_token_and_settings(self):
        refresher = Refresher(ACCOUNT, lambda: httpx.Client())
        with pytest.raises(AuthError, match="cannot be renewed"):
            refresher(OAuth2Token("old", refresh_token="r0"))
        with pytest.raises(AuthError, match="cannot be renewed"):
            Refresher(
                replace(ACCOUNT, oauth=OAuthSettings(client_id="c", issuer=ISSUER)), httpx.Client
            )(OAuth2Token("old"))


class TestCommands:
    def test_add_signs_in_and_stores_the_token(
        self, invoke, make_context, environ, secrets, server, auth_server
    ):
        context = make_context()
        context.open_url = browse
        assert (
            run(["account", "add", "work", "--url", URL, "--auth", "oauth"], context=context) == 0
        )
        stored = load_config(config_path(environ)).accounts["work"]
        assert stored.auth == "oauth"
        assert stored.oauth == OAuthSettings(client_id="registered", issuer=ISSUER)
        token = OAuthTokenStore(secrets, "work").load()
        assert token is not None
        assert token.access_token == "at1"
        # Later commands present the stored token.
        assert invoke("server", "echo").code == 0
        assert server.requests

    def test_add_by_device_without_a_browser(self, invoke, secrets, auth_server):
        result = invoke(
            "account",
            "add",
            "work",
            "--url",
            URL,
            "--auth",
            "oauth",
            "--device",
            "--client-id",
            "mine",
            "--scope",
            "jmap",
            "--no-browser",
        )
        assert result.code == 0, result.err
        assert "enter WXYZ" in result.err

    def test_login_renews_and_keeps_new_settings(self, invoke, environ, secrets, auth_server):
        invoke("account", "add", "work", "--url", URL, "--auth", "oauth", "--device")
        assert invoke("account", "login", "--device").code == 0
        token = OAuthTokenStore(secrets, "work").load()
        assert token is not None
        assert token.access_token == "at2"
        stored = load_config(config_path(environ)).accounts["work"]
        assert stored.oauth.client_id == "registered"

    def test_login_saves_a_newly_registered_client(self, invoke, environ, secrets, auth_server):
        oauth = AccountConfig(url=URL, auth="oauth", oauth=OAuthSettings(scope="jmap"))
        save_config(Config(accounts={"work": oauth}), config_path(environ))
        assert invoke("account", "login", "--device", "--no-verify").code == 0
        assert load_config(config_path(environ)).accounts["work"].oauth == OAuthSettings(
            client_id="registered", scope="jmap", issuer=ISSUER
        )

    def test_a_stored_token_is_used_as_bearer(self, invoke, environ, secrets, server):
        save_config(
            Config(accounts={"work": AccountConfig(url=URL, auth="oauth")}), config_path(environ)
        )
        secrets.set("work", "oauth", dumps_token(OAuth2Token("stored")))
        seen: list[str] = []

        def record(request: httpx.Request) -> None:
            seen.append(request.headers.get("authorization", ""))

        server.intercept = record
        assert invoke("server", "echo").code == 0
        assert "Bearer stored" in seen
