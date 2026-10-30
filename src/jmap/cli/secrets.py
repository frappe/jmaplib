"""Where credentials are kept: the OS keyring, or memory when there is none.

``keyring`` is imported on first use, so a command that needs no secret never
pays for loading a backend, and a machine without one fails only when a secret
is actually wanted - with :class:`KeyringUnavailableError`, which the caller
turns into a note and a fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, Protocol

from jmap.cli.errors import CLIError

if TYPE_CHECKING:
    from types import ModuleType

SERVICE: Final = "jmaplib"

SecretKind = Literal["password", "token", "oauth", "push"]

#: The kinds an account signs in with. ``oauth`` holds the token, refresh token
#: and expiry as JSON. ``push``, not one of them, holds a push subscription's keys.
KINDS: Final[tuple[SecretKind, ...]] = ("password", "token", "oauth")


class KeyringUnavailableError(CLIError):
    """No usable keyring backend on this machine."""


class SecretStore(Protocol):
    def get(self, account: str, kind: SecretKind) -> str | None: ...

    def set(self, account: str, kind: SecretKind, value: str) -> None: ...

    def delete(self, account: str, kind: SecretKind) -> None: ...


class KeyringStore:
    """Secrets in the OS keyring, under service ``jmaplib`` and user ``<account>:<kind>``."""

    def get(self, account: str, kind: SecretKind) -> str | None:
        keyring = _keyring()
        try:
            found: str | None = keyring.get_password(SERVICE, _user(account, kind))
        except _backend_errors(keyring) as error:
            raise _unavailable(error) from error
        return found

    def set(self, account: str, kind: SecretKind, value: str) -> None:
        keyring = _keyring()
        try:
            keyring.set_password(SERVICE, _user(account, kind), value)
        except _backend_errors(keyring) as error:
            raise _unavailable(error) from error

    def delete(self, account: str, kind: SecretKind) -> None:
        keyring = _keyring()
        try:
            keyring.delete_password(SERVICE, _user(account, kind))
        except keyring.errors.PasswordDeleteError:
            pass  # nothing stored is what was wanted
        except _backend_errors(keyring) as error:
            raise _unavailable(error) from error


class MemoryStore:
    """Secrets for this process only: ``--no-keyring``, and tests."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, SecretKind], str] = {}

    def get(self, account: str, kind: SecretKind) -> str | None:
        return self.values.get((account, kind))

    def set(self, account: str, kind: SecretKind, value: str) -> None:
        self.values[account, kind] = value

    def delete(self, account: str, kind: SecretKind) -> None:
        self.values.pop((account, kind), None)


def _keyring() -> ModuleType:
    try:
        import keyring
    except ImportError as error:
        raise _unavailable(error) from error
    return keyring


def _backend_errors(keyring: ModuleType) -> tuple[type[Exception], ...]:
    return (keyring.errors.NoKeyringError, keyring.errors.InitError, keyring.errors.KeyringError)


def _user(account: str, kind: SecretKind) -> str:
    return f"{account}:{kind}"


def _unavailable(error: BaseException) -> KeyringUnavailableError:
    return KeyringUnavailableError(
        f"no usable keyring ({error or type(error).__name__})",
        hint="set JMAP_PASSWORD or JMAP_TOKEN, or pass --no-keyring",
    )
