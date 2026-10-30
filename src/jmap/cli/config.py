"""The configuration file and how one named account is resolved from it.

A *named account* is one server and one credential. Its settings come from three
places, and for each field the most specific wins: a command-line flag, then an
environment variable, then the file. Secrets never live in the file; they are in
the keyring (see :mod:`jmap.cli.secrets`).
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jmap.cli.errors import ConfigError
from jmap.core.narrow import is_object

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

OutputName = Literal["auto", "table", "plain", "json", "jsonl", "ids"]
ColorName = Literal["auto", "always", "never"]
AuthKind = Literal["password", "token"]

#: Keys that would put a secret in a plain-text file.
_SECRET_KEYS = ("password", "token")

#: The name given to an account built from ``$JMAP_URL`` alone.
ENV_ACCOUNT = "env"


class _Strict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class Defaults(_Strict):
    output: OutputName = "auto"
    color: ColorName = "auto"
    pager: Literal["auto", "never"] = "auto"
    editor: str = ""
    timeout: float = 30.0


class AccountConfig(_Strict):
    """One ``[accounts.NAME]`` table."""

    address: str = ""
    url: str = ""
    auth: AuthKind = "password"
    username: str = ""
    #: The JMAP account inside the session, by id or name; empty follows
    #: the server's ``primaryAccounts``.
    account_id: str = ""
    identity: str = ""
    experimental: bool = False
    timeout: float | None = None
    trusted_srv_targets: list[str] = Field(default_factory=lambda: [])
    ca_bundle: str = ""
    insecure: bool = False
    send_move_to_sent: bool = True

    @model_validator(mode="before")
    @classmethod
    def _no_secrets(cls, data: Any) -> Any:
        if is_object(data):
            for key in _SECRET_KEYS:
                if key in data:
                    raise ValueError(
                        f"{key!r} does not belong in the config file; "
                        "store it with 'jmap account login NAME'"
                    )
        return data


class Config(_Strict):
    default_account: str = ""
    defaults: Defaults = Field(default_factory=Defaults)
    accounts: dict[str, AccountConfig] = Field(default_factory=lambda: {})


def load_config(path: Path) -> Config:
    """Read ``path``; a missing file is an empty configuration."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    try:
        return Config.model_validate(tomllib.loads(text))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from error
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        message = first["msg"].removeprefix("Value error, ")
        # A TOML document is always a table, so an error always has a location.
        raise ConfigError(f"{path}: {where}: {message}") from error


def save_config(config: Config, path: Path) -> None:
    """Write ``path`` atomically, readable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_defaults=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".toml")
    try:
        with os.fdopen(handle, "wb") as stream:
            tomli_w.dump(data, stream)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


@dataclass(frozen=True, slots=True)
class Overrides:
    """The global flags that change which account is used and how."""

    account: str | None = None
    account_id: str | None = None
    experimental: bool = False
    insecure: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedAccount:
    """One named account with every override applied."""

    name: str
    address: str
    url: str
    auth: AuthKind
    username: str
    account_id: str
    identity: str = ""
    experimental: bool = False
    timeout: float = 30.0
    trusted_srv_targets: tuple[str, ...] = ()
    ca_bundle: str = ""
    insecure: bool = False
    send_move_to_sent: bool = True
    #: Built from the environment rather than read from the file.
    synthetic: bool = field(default=False, compare=False)


def resolve_account(
    config: Config, overrides: Overrides, environ: Mapping[str, str]
) -> ResolvedAccount:
    """Pick the named account and apply flags and environment to it."""
    name = overrides.account or environ.get("JMAP_ACCOUNT") or config.default_account
    if not name and len(config.accounts) == 1:
        name = next(iter(config.accounts))
    stored = config.accounts.get(name) if name else None
    if stored is not None:
        resolved = from_config(name, stored, config.defaults)
    elif environ.get("JMAP_URL"):
        resolved = _from_environment(name or ENV_ACCOUNT, environ, config.defaults)
    else:
        raise _no_account(name, config)
    return _apply(resolved, overrides, environ)


def from_config(name: str, account: AccountConfig, defaults: Defaults) -> ResolvedAccount:
    """An account as stored, before flags or environment apply."""
    return ResolvedAccount(
        name=name,
        address=account.address,
        url=account.url,
        auth=account.auth,
        username=account.username or account.address,
        account_id=account.account_id,
        identity=account.identity,
        experimental=account.experimental,
        timeout=account.timeout if account.timeout is not None else defaults.timeout,
        trusted_srv_targets=tuple(account.trusted_srv_targets),
        ca_bundle=account.ca_bundle,
        insecure=account.insecure,
        send_move_to_sent=account.send_move_to_sent,
    )


def _from_environment(name: str, environ: Mapping[str, str], defaults: Defaults) -> ResolvedAccount:
    token_only = bool(environ.get("JMAP_TOKEN")) and not environ.get("JMAP_PASSWORD")
    return ResolvedAccount(
        name=name,
        address="",
        url=environ["JMAP_URL"],
        auth="token" if token_only else "password",
        username="",
        account_id="",
        timeout=defaults.timeout,
        synthetic=True,
    )


def _apply(
    resolved: ResolvedAccount, overrides: Overrides, environ: Mapping[str, str]
) -> ResolvedAccount:
    return replace(
        resolved,
        url=environ.get("JMAP_URL") or resolved.url,
        username=environ.get("JMAP_USER") or resolved.username,
        account_id=overrides.account_id or resolved.account_id,
        experimental=overrides.experimental
        or _truthy(environ.get("JMAP_EXPERIMENTAL"))
        or resolved.experimental,
        insecure=overrides.insecure or _truthy(environ.get("JMAP_INSECURE")) or resolved.insecure,
    )


def _no_account(name: str | None, config: Config) -> ConfigError:
    known = ", ".join(sorted(config.accounts))
    if name:
        return ConfigError(
            f"no account named {name!r}" + (f" (known: {known})" if known else ""),
            hint="see 'jmap account list'",
        )
    if config.accounts:
        return ConfigError(
            f"several accounts and no default (known: {known})",
            hint="pass -a NAME, or run 'jmap account set-default NAME'",
        )
    return ConfigError(
        "no account is configured",
        hint="run 'jmap account add NAME --url URL', or set JMAP_URL",
    )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
