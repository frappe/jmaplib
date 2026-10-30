"""Exit codes, the command's own errors, and how any error is reported.

Every failure leaves as one ``jmap: <message>`` line on stderr and an exit code
that says which kind of failure it was, so a script can tell "wrong password"
(3) from "server down" (4) from "no such mailbox" (9) without parsing text.
"""

from __future__ import annotations

import json
import traceback
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from jmap.auth import OAuthError
from jmap.batch import NoAccountError, ReadOnlyAccountError
from jmap.capabilities.registry import UnsupportedMethodError
from jmap.core.errors import (
    URN_LIMIT,
    URN_NOT_JSON,
    URN_NOT_REQUEST,
    URN_UNKNOWN_CAPABILITY,
    AuthenticationError,
    BatchTooLargeError,
    CapabilityFieldError,
    CapabilityNotSupportedError,
    JMAPError,
    MethodError,
    RequestError,
    TransportError,
)
from jmap.core.ids import InvalidIdError
from jmap.core.ijson import IJSONError
from jmap.core.patch import InvalidKeywordError, InvalidPatchError
from jmap.core.session import InsecureEndpointError, MalformedSessionError
from jmap.discovery import DiscoveryUnavailableError, UnconfirmedSRVTargetError
from jmap.mail.addresses import InvalidAddressError
from jmap.mail.compose import DraftParseError
from jmap.mail.query import QueryError
from jmap.sync.changes import ResyncRequiredError

if TYPE_CHECKING:
    from rich.console import Console


class ExitCode(IntEnum):
    OK = 0
    FAILURE = 1
    USAGE = 2
    AUTH = 3
    CONNECTION = 4
    REQUEST = 5
    METHOD = 6
    UNSUPPORTED = 7
    PARTIAL = 8
    NOT_FOUND = 9
    CONFIG = 10
    INTERRUPTED = 130


class CLIError(JMAPError):
    """An error the command raises itself, carrying its exit code and a hint.

    A :class:`JMAPError`, so the library's one ``except`` still catches everything.
    """

    code: ExitCode = ExitCode.FAILURE

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.hint = hint
        super().__init__(message)


class UsageError(CLIError):
    code = ExitCode.USAGE


class AuthError(CLIError):
    code = ExitCode.AUTH


class UnsupportedError(CLIError):
    code = ExitCode.UNSUPPORTED


class PartialFailureError(CLIError):
    """A ``/set`` changed some objects and refused others."""

    code = ExitCode.PARTIAL


class NotFoundError(CLIError):
    code = ExitCode.NOT_FOUND


class ConfigError(CLIError):
    code = ExitCode.CONFIG


#: Library errors and their exit codes. Order matters: the first match wins, so
#: a subclass must come before its base.
_CODES: Final[tuple[tuple[tuple[type[BaseException], ...], ExitCode], ...]] = (
    (
        (
            ValidationError,
            InvalidIdError,
            CapabilityFieldError,
            BatchTooLargeError,
            ReadOnlyAccountError,
            NoAccountError,
            QueryError,
            DraftParseError,
            InvalidAddressError,
            InvalidKeywordError,
            InvalidPatchError,
        ),
        ExitCode.USAGE,
    ),
    ((AuthenticationError, OAuthError), ExitCode.AUTH),
    (
        (
            TransportError,
            UnconfirmedSRVTargetError,
            InsecureEndpointError,
            MalformedSessionError,
            DiscoveryUnavailableError,
        ),
        ExitCode.CONNECTION,
    ),
    ((RequestError, IJSONError), ExitCode.REQUEST),
    ((MethodError,), ExitCode.METHOD),
    ((CapabilityNotSupportedError, UnsupportedMethodError), ExitCode.UNSUPPORTED),
    ((ResyncRequiredError,), ExitCode.CONFIG),
    ((KeyboardInterrupt,), ExitCode.INTERRUPTED),
)

#: The RFC 8620 problem types in words, for a server whose detail says nothing useful.
_PROBLEMS: Final = {
    URN_NOT_REQUEST: "the server could not read the request",
    URN_NOT_JSON: "the server did not accept the request as JSON",
    URN_UNKNOWN_CAPABILITY: "the server does not know a capability the request uses",
    URN_LIMIT: "the request is over one of the server's limits",
}

#: What the client's namespaces raise for a capability the server lacks.
_NOT_OFFERED: Final = "this server does not offer"


def exit_code_for(error: BaseException) -> ExitCode:
    if isinstance(error, CLIError):
        return error.code
    for types, code in _CODES:
        if isinstance(error, types):
            return code
    if isinstance(error, AttributeError) and str(error).startswith(_NOT_OFFERED):
        return ExitCode.UNSUPPORTED
    return ExitCode.FAILURE


def report(error: BaseException, err: Console, *, json_mode: bool, verbose: int) -> ExitCode:
    """Write ``error`` to stderr and return the exit code it maps to."""
    code = exit_code_for(error)
    message = _message(error, code)
    hint = _hint(error)
    err.print(f"jmap: {message}", markup=False, highlight=False)
    if hint:
        err.print(f"hint: {hint}", markup=False, highlight=False)
    if verbose:
        for line in _details(error):
            err.print(line, markup=False, highlight=False)
    if json_mode:
        # Straight to the file: rich would wrap the line and break the JSON.
        err.file.write(json.dumps({"error": _json_error(error, code, message, hint)}) + "\n")
    return code


def _message(error: BaseException, code: ExitCode) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, ValidationError):
        return _validation_message(error)
    if isinstance(error, RequestError) and _echoes_request(error.detail):
        head = error.title or _PROBLEMS.get(error.type, error.type)
        return f"{head} [{error.type}; HTTP {error.status}]"
    if isinstance(error, IJSONError):
        return f"the server's answer is not valid JSON: {error}"
    text = str(error) or type(error).__name__
    if code is ExitCode.FAILURE and not isinstance(error, CLIError):
        return f"{type(error).__name__}: {text} (run with -v for the traceback)"
    return text


def _validation_message(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return f"{where}: {first['msg']}" if where else first["msg"]


def _hint(error: BaseException) -> str | None:
    if isinstance(error, CLIError):
        return error.hint
    if isinstance(error, UnconfirmedSRVTargetError):
        hosts = " ".join(f"--trust-srv {target.host}" for target in error.targets)
        return f"if you trust it, add the account again with {hosts}"
    if isinstance(error, AuthenticationError):
        return "check the credential with 'jmap account login NAME'"
    if isinstance(error, RequestError) and error.type == URN_NOT_REQUEST:
        return "an argument may have a value the server cannot read, such as a state or an id"
    if isinstance(error, IJSONError):
        return "this is a fault in the server; -v shows where it was found"
    return None


def _echoes_request(detail: str | None) -> bool:
    """Whether ``detail`` is only the request sent back, as Stalwart's is."""
    try:
        echoed = json.loads(detail or "")
    except ValueError:
        return False
    return isinstance(echoed, dict) and "methodCalls" in echoed


def _details(error: BaseException) -> list[str]:
    """What ``-v`` adds: the server's problem fields, then the traceback chain."""
    lines: list[str] = []
    if isinstance(error, RequestError):
        lines.append(
            f"type={error.type} status={error.status} "
            f"retry_after={error.retry_after} limit={error.limit}"
        )
    if isinstance(error, MethodError):
        lines.append(f"method error arguments: {json.dumps(error.arguments)}")
    lines += "".join(traceback.format_exception(error)).rstrip("\n").splitlines()
    return lines


def _json_error(error: BaseException, code: ExitCode, message: str, hint: str | None) -> Any:
    body: dict[str, Any] = {
        "code": int(code),
        "kind": code.name.lower(),
        "message": message,
        "type": error.type
        if isinstance(error, RequestError | MethodError)
        else type(error).__name__,
        "hint": hint,
    }
    if isinstance(error, RequestError):
        body["status"] = error.status
        body["retryAfter"] = error.retry_after
    return body
