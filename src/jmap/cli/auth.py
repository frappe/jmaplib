"""Turning a named account into the credential the client presents.

The secret is looked for in a fixed order, and the first source that has one
wins: piped stdin (``--password-stdin``), the environment, the keyring, then a
prompt. A secret is never accepted as a command-line argument, where every user
on the machine could read it from the process list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from jmap.auth import BasicAuth, BearerAuth
from jmap.cli.errors import AuthError
from jmap.cli.secrets import KeyringUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import httpx

    from jmap.cli.config import ResolvedAccount
    from jmap.cli.prompts import Prompter
    from jmap.cli.secrets import SecretStore

#: The environment variable each kind of secret may come from.
ENV_VARS: Final = {"password": "JMAP_PASSWORD", "token": "JMAP_TOKEN"}


class Credentials:
    """Finds the secret for one account, remembering where it came from."""

    #: ``stdin``, ``environment``, ``keyring`` or ``prompt``, once found.
    source: str

    def __init__(
        self,
        *,
        secrets: SecretStore,
        environ: Mapping[str, str],
        prompter: Prompter,
        warn: Callable[[str], None],
        stdin_secret: str | None = None,
    ) -> None:
        self._secrets = secrets
        self._environ = environ
        self._prompter = prompter
        self._warn = warn
        self._stdin_secret = stdin_secret
        self.source = ""

    def auth(self, account: ResolvedAccount) -> httpx.Auth:
        """The ``httpx.Auth`` for ``account``, or :class:`AuthError` with the remedies."""
        return make_auth(account, self.secret(account))

    def secret(self, account: ResolvedAccount) -> str:
        """The first secret found for ``account``; :attr:`source` says where."""
        kind = account.auth
        if self._stdin_secret:
            self.source = "stdin"
            return self._stdin_secret
        from_environment = self._environ.get(ENV_VARS[kind])
        if from_environment:
            self.source = "environment"
            return from_environment
        stored = self._stored(account)
        if stored:
            self.source = "keyring"
            return stored
        if self._prompter.interactive:
            self.source = "prompt"
            return self._prompter.secret(f"{kind.capitalize()} for {account.name}")
        raise AuthError(
            f"no {kind} for account {account.name!r}",
            hint=f"run 'jmap account login {account.name}', set {ENV_VARS[kind]}, "
            f"or pipe it to --{kind}-stdin",
        )

    def _stored(self, account: ResolvedAccount) -> str | None:
        if account.synthetic:
            return None
        try:
            return self._secrets.get(account.name, account.auth)
        except KeyringUnavailableError as error:
            self._warn(f"{error}; trying the other sources")
            return None


def make_auth(account: ResolvedAccount, secret: str) -> httpx.Auth:
    if account.auth == "token":
        return BearerAuth(secret)
    if not account.username:
        raise AuthError(
            f"account {account.name!r} has no username",
            hint="set JMAP_USER, or add the account with --username",
        )
    return BasicAuth(account.username, secret)
