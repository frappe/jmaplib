"""Everything a command needs, in one object, built once per run.

Every input from outside - environment, streams, keyring, prompts, the network -
is a constructor argument, so a test builds a context around a fake server and
in-memory streams and drives the real command tree through it. Nothing is opened
until a command asks: ``jmap account list`` never touches the network.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from rich.console import Console

from jmap.cli.auth import Credentials
from jmap.cli.config import Config, Overrides, load_config, resolve_account, save_config
from jmap.cli.connect import http_client, open_client, select_account
from jmap.cli.errors import CLIError, UnsupportedError
from jmap.cli.ids import MailboxIndex
from jmap.cli.output import OutputFormat, OutputOptions, Renderer
from jmap.cli.paths import config_path
from jmap.cli.prompts import TerminalPrompter
from jmap.cli.secrets import KeyringStore, MemoryStore
from jmap.defaults import default_registry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import TextIO

    import httpx

    from jmap.capabilities.registry import Registry
    from jmap.capabilities.spec import CapabilitySpec
    from jmap.cli.config import ResolvedAccount
    from jmap.cli.prompts import Prompter
    from jmap.cli.secrets import SecretStore
    from jmap.client import JMAPClient

    HTTPFactory = Callable[[ResolvedAccount, int, Console], httpx.Client]

#: The loggers ``-vv`` turns up to DEBUG.
_WIRE_LOGGERS: Final = ("httpx", "httpcore")


@dataclass(frozen=True, slots=True)
class GlobalFlags:
    """The options every command accepts, wherever they appear on the line."""

    account: str | None = None
    account_id: str | None = None
    output: OutputFormat | None = None
    quiet: bool = False
    verbose: int = 0
    color: str | None = None
    no_keyring: bool = False
    experimental: bool = False
    insecure: bool = False
    config: str | None = None
    yes: bool = False


class CLIContext:
    """The state of one run of the command."""

    environ: Mapping[str, str]
    stdin: TextIO
    secrets: SecretStore
    http_factory: HTTPFactory
    registry: Registry
    flags: GlobalFlags
    out: Console
    err: Console
    prompter: Prompter
    options: OutputOptions
    #: Set by commands that read the secret from stdin, before connecting.
    stdin_secret: str | None

    def __init__(
        self,
        *,
        environ: Mapping[str, str],
        stdin: TextIO,
        stdout: TextIO,
        stderr: TextIO,
        secrets: SecretStore | None = None,
        prompter: Prompter | None = None,
        http_factory: HTTPFactory | None = None,
        registry: Registry | None = None,
        terminal: bool | None = None,
        width: int | None = None,
    ) -> None:
        self.environ = environ
        self.stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
        self._terminal = terminal
        self._width = width
        self.secrets = secrets or KeyringStore()
        self.http_factory = http_factory or _default_http
        self.registry = registry or default_registry()
        self.flags = GlobalFlags()
        self.out = self._console(stdout, "auto")
        self.err = self._console(stderr, "auto")
        self.prompter = prompter or TerminalPrompter(self.err, stdin)
        self.options = OutputOptions()
        self.stdin_secret = None
        self._config: Config | None = None
        self._account: ResolvedAccount | None = None
        self._client: JMAPClient | None = None
        self._http: httpx.Client | None = None
        self._mailboxes: MailboxIndex | None = None
        self._log_handler: logging.Handler | None = None

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> CLIContext:
        return cls(environ=environ, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)

    def configure(self, flags: GlobalFlags) -> None:
        """Apply the global options, once they are parsed."""
        self.flags = flags
        defaults = self._config_or_defaults().defaults
        color = flags.color or defaults.color
        self.out = self._console(self._stdout, color)
        self.err = self._console(self._stderr, color)
        if flags.no_keyring:
            self.secrets = MemoryStore()
        self.options = OutputOptions(
            format=flags.output or OutputFormat(defaults.output),
            quiet=flags.quiet,
            pager="" if defaults.pager == "never" else self._pager(),
        )
        if flags.verbose >= 2:
            self._log_wire()

    # -- configuration ------------------------------------------------------ #
    @property
    def config_path(self) -> Path:
        return config_path({**self.environ, **_given("JMAP_CONFIG", self.flags.config)})

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = load_config(self.config_path)
        return self._config

    def save_config(self, config: Config) -> None:
        save_config(config, self.config_path)
        self._config = config

    @property
    def account(self) -> ResolvedAccount:
        """The named account this run uses."""
        if self._account is None:
            overrides = Overrides(
                account=self.flags.account,
                account_id=self.flags.account_id,
                experimental=self.flags.experimental,
                insecure=self.flags.insecure,
            )
            self._account = resolve_account(self.config, overrides, self.environ)
        return self._account

    def use_account(self, name: str | None) -> None:
        """Switch to another named account; the next :meth:`client` connects to it."""
        if name is None:
            return
        self.close_client()
        self.flags = replace(self.flags, account=name)
        self._account = None

    # -- the connection ----------------------------------------------------- #
    def credentials(self) -> Credentials:
        return Credentials(
            secrets=self.secrets,
            environ=self.environ,
            prompter=self.prompter,
            warn=self.renderer.note,
            stdin_secret=self.stdin_secret,
        )

    def connect(self, account: ResolvedAccount, auth: httpx.Auth) -> JMAPClient:
        """Open a client for ``account``; the context closes it."""
        self.close_client()
        http = self.http_factory(account, self.flags.verbose, self.err)
        try:
            self._client = open_client(
                account, auth=auth, http=http, prompter=self.prompter, registry=self.registry
            )
        except BaseException:
            http.close()
            raise
        self._http = http
        return self._client

    def client(self) -> JMAPClient:
        """The client for this run's account, connecting on first use."""
        if self._client is None:
            account = self.account
            return self.connect(account, self.credentials().auth(account))
        return self._client

    def require(self, *methods: str) -> JMAPClient:
        """The client, once every method in ``methods`` is known to work on this server.

        Asking for an experimental capability's method is the opt-in: if that is
        all that stands in the way, it is switched on rather than refused.
        """
        client = self.client()
        missing = [name for name in methods if not client.capabilities.supports(name)]
        if missing and not client.experimental and any(map(self._opt_in_fixes, missing)):
            client.experimental = True
            select_account(client, client.default_account)
            missing = [name for name in missing if not client.capabilities.supports(name)]
        if missing:
            raise self._unsupported(client, missing[0])
        return client

    def mailboxes(self) -> MailboxIndex:
        if self._mailboxes is None:
            self._mailboxes = MailboxIndex.load(self.require("Mailbox/get"))
        return self._mailboxes

    # -- output ------------------------------------------------------------- #
    @property
    def renderer(self) -> Renderer:
        return Renderer(self.out, self.err, self.options)

    def confirm(self, question: str) -> None:
        """Go on only if ``--yes`` was given or the person agrees."""
        if self.flags.yes:
            return
        if not self.prompter.interactive:
            raise CLIError(f"{question} (refusing without a terminal)", hint="pass --yes")
        if not self.prompter.confirm(question):
            raise CLIError("cancelled")

    def close_client(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._mailboxes = None
        if self._http is not None:
            self._http.close()
            self._http = None

    def close(self) -> None:
        self.close_client()
        if self._log_handler is not None:
            for name in _WIRE_LOGGERS:
                logging.getLogger(name).removeHandler(self._log_handler)
            self._log_handler = None

    # -- helpers ------------------------------------------------------------ #
    def _console(self, file: TextIO, color: str) -> Console:
        no_color = color == "never" or (color == "auto" and "NO_COLOR" in self.environ)
        return Console(
            file=file,
            force_terminal=True if color == "always" else self._terminal,
            no_color=no_color,
            width=self._width,
            highlight=False,
        )

    def _config_or_defaults(self) -> Config:
        try:
            return self.config
        except CLIError:
            return Config()  # reported when a command reads the config itself

    def _pager(self) -> str:
        fallback = "more" if os.name == "nt" else "less -FRX"
        return self.environ.get("JMAP_PAGER") or self.environ.get("PAGER") or fallback

    def _log_wire(self) -> None:
        handler = logging.StreamHandler(self.err.file)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        for name in _WIRE_LOGGERS:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)
            logger.addHandler(handler)
        self._log_handler = handler

    def _opt_in_fixes(self, method: str) -> bool:
        spec = _spec_owning(self.registry, method)
        return spec is not None and spec.experimental

    def _unsupported(self, client: JMAPClient, method: str) -> UnsupportedError:
        spec = _spec_owning(self.registry, method)
        if spec is not None and spec.urn not in client.capabilities.advertised:
            return UnsupportedError(
                f"this server does not advertise {spec.urn}, needed by {method}"
            )
        return UnsupportedError(f"this server does not offer {method}")


def _spec_owning(registry: Registry, method: str) -> CapabilitySpec | None:
    for urn in sorted(registry.urns):
        for spec in registry.specs_for(urn):
            if spec.method(method) is not None:
                return spec
    return None


def _given(key: str, value: str | None) -> dict[str, str]:
    return {key: value} if value else {}


def _default_http(account: ResolvedAccount, verbose: int, err: Console) -> httpx.Client:
    return http_client(account, verbose=verbose, err=err)
