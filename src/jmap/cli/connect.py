"""Opening a connection for one named account.

The session is fetched on every run and never cached: a one-shot command opens a
connection anyway, staleness only shows in a response, and the session lists
accounts and URLs that should not sit on disk. :func:`open_client` is the one
place to change if that ever needs revisiting.
"""

from __future__ import annotations

import json
import ssl
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit, urlunsplit

import httpx

from jmap.cli.errors import NotFoundError
from jmap.cli.prompts import srv_confirmation
from jmap.client import JMAPClient
from jmap.core.ids import Id
from jmap.core.narrow import is_list, is_object
from jmap.discovery import WELL_KNOWN_PATH

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console

    from jmap.capabilities.registry import Registry
    from jmap.cli.config import ResolvedAccount
    from jmap.cli.prompts import Prompter
    from jmap.core.session import Session

#: Where the ``-v`` request hook leaves its start time for the response hook.
_STARTED: Final = "jmap.cli.started"


def http_client(
    account: ResolvedAccount,
    *,
    verbose: int,
    err: Console,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """The HTTP client for ``account``: TLS settings, timeout and ``-v`` tracing."""
    hooks: dict[str, list[Any]] = {}
    if verbose:
        tracer = _Tracer(err)
        hooks = {"request": [tracer.request], "response": [tracer.response]}
    return httpx.Client(
        verify=_verify(account, err),
        timeout=account.timeout,
        follow_redirects=True,
        event_hooks=hooks,
        transport=transport,
    )


def open_client(
    account: ResolvedAccount,
    *,
    auth: httpx.Auth,
    http: httpx.Client,
    prompter: Prompter,
    registry: Registry | None = None,
    on_trust: Callable[[str], None] | None = None,
    client_class: type[JMAPClient] = JMAPClient,
) -> JMAPClient:
    """Fetch the session, then select the JMAP account the settings name.

    ``on_trust`` hears each SRV host the person agrees to at the prompt.
    """
    options: dict[str, Any] = {
        "auth": auth,
        "http": http,
        "registry": registry,
        "experimental": account.experimental,
    }
    if account.url:
        client = client_class.connect(session_url(account.url), **options)
    else:
        confirm = srv_confirmation(prompter, account.trusted_srv_targets, on_trust)
        client = client_class.discover(account.address, confirm_srv_target=confirm, **options)
    if account.account_id:
        select_account(client, find_account(client.session, account.account_id))
    return client


def session_url(url: str) -> str:
    """``url``, or its ``/.well-known/jmap`` when it names only a host.

    A server's root is usually its web app, which answers with HTML rather
    than a session; RFC 8620 §2.2 puts the session behind the well-known path.
    """
    parts = urlsplit(url)
    if parts.path.strip("/"):
        return url
    return urlunsplit(parts._replace(path=WELL_KNOWN_PATH))


def find_account(session: Session, wanted: str) -> Id:
    """A JMAP account by id, or by its unique name ignoring case."""
    if wanted in session.accounts:
        return Id(wanted)
    named = [
        account_id
        for account_id, account in session.accounts.items()
        if account.name.lower() == wanted.lower()
    ]
    if len(named) == 1:
        return named[0]
    listed = ", ".join(f"{key} ({account.name})" for key, account in session.accounts.items())
    problem = "several JMAP accounts are named" if named else "no JMAP account"
    raise NotFoundError(
        f"{problem} {wanted!r}; this session has: {listed}",
        hint="pass --account-id with an id from 'jmap account show'",
    )


def select_account(client: JMAPClient, account_id: Id | None) -> None:
    """Point every call, upload and download at ``account_id``."""
    client.default_account = account_id
    client.capabilities = client.registry.resolve(
        client.session, account_id, experimental=client.experimental
    )


def _verify(account: ResolvedAccount, err: Console) -> ssl.SSLContext | bool:
    if account.insecure:
        err.print("jmap: warning: TLS certificates are not being verified", markup=False)
        return False
    if account.ca_bundle:
        return ssl.create_default_context(cafile=account.ca_bundle)
    return True


class _Tracer:
    """``-v``: one line per request and per response, never the credentials."""

    def __init__(self, err: Console) -> None:
        self._err = err

    def request(self, request: httpx.Request) -> None:
        request.extensions[_STARTED] = time.monotonic()
        methods = _method_names(request)
        suffix = f" [{', '.join(methods)}]" if methods else ""
        self._err.print(f"-> {request.method} {request.url}{suffix}", markup=False)

    def response(self, response: httpx.Response) -> None:
        started = response.request.extensions.get(_STARTED, time.monotonic())
        elapsed = (time.monotonic() - started) * 1000
        self._err.print(f"<- {response.status_code} ({elapsed:.0f} ms)", markup=False)


def _method_names(request: httpx.Request) -> list[str]:
    if request.headers.get("content-type") != "application/json":
        return []
    try:
        body = json.loads(request.content)
    except ValueError:
        return []
    calls = body.get("methodCalls") if is_object(body) else None
    if not is_list(calls):
        return []
    return [str(call[0]) for call in calls if is_list(call) and call]
