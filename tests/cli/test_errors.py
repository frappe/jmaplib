"""Exit codes and the one-line error report."""

from __future__ import annotations

import io
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from rich.console import Console

from jmap.auth import OAuthError
from jmap.batch import NoAccountError
from jmap.capabilities.registry import UnsupportedMethodError
from jmap.cli.errors import (
    AuthError,
    ConfigError,
    ExitCode,
    NotFoundError,
    PartialFailureError,
    UnsupportedError,
    UsageError,
    exit_code_for,
    report,
)
from jmap.core.errors import (
    AuthenticationError,
    CapabilityNotSupportedError,
    MethodError,
    RequestError,
    ServerPartialFailError,
    TransportError,
)
from jmap.core.ids import InvalidIdError
from jmap.core.ijson import DuplicateKeyError
from jmap.discovery import SRVTarget, UnconfirmedSRVTargetError
from jmap.sync.changes import ResyncRequiredError


def validation_error(value: object = "x") -> ValidationError:
    try:
        TypeAdapter(int).validate_python(value)
    except ValidationError as error:
        return error
    raise AssertionError("unreachable")  # pragma: no cover


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (UsageError("u"), ExitCode.USAGE),
        (AuthError("a"), ExitCode.AUTH),
        (UnsupportedError("u"), ExitCode.UNSUPPORTED),
        (PartialFailureError("p"), ExitCode.PARTIAL),
        (NotFoundError("n"), ExitCode.NOT_FOUND),
        (ConfigError("c"), ExitCode.CONFIG),
        (validation_error(), ExitCode.USAGE),
        (InvalidIdError("x!", "bad character"), ExitCode.USAGE),
        (NoAccountError("Email/get", "urn"), ExitCode.USAGE),
        (AuthenticationError("no"), ExitCode.AUTH),
        (OAuthError("no"), ExitCode.AUTH),
        (TransportError("down"), ExitCode.CONNECTION),
        (UnconfirmedSRVTargetError("example.com", ()), ExitCode.CONNECTION),
        (RequestError("about:blank", status=500), ExitCode.REQUEST),
        (MethodError("invalidArguments", "c1", {}), ExitCode.METHOD),
        (ServerPartialFailError("serverPartialFail", "c1", {}), ExitCode.METHOD),
        (CapabilityNotSupportedError("urn:x"), ExitCode.UNSUPPORTED),
        (UnsupportedMethodError("Foo/get"), ExitCode.UNSUPPORTED),
        (AttributeError("this server does not offer mail"), ExitCode.UNSUPPORTED),
        (AttributeError("something else"), ExitCode.FAILURE),
        (ResyncRequiredError("Email", "s1"), ExitCode.CONFIG),
        (KeyboardInterrupt(), ExitCode.INTERRUPTED),
        (RuntimeError("boom"), ExitCode.FAILURE),
    ],
)
def test_exit_codes(error: BaseException, code: ExitCode) -> None:
    assert exit_code_for(error) is code


def reported(error: BaseException, *, json_mode: bool = False, verbose: int = 0) -> list[str]:
    stream = io.StringIO()
    code = report(error, Console(file=stream, width=200), json_mode=json_mode, verbose=verbose)
    assert code is exit_code_for(error)
    return stream.getvalue().splitlines()


class TestReport:
    def test_one_line_with_a_hint(self):
        lines = reported(NotFoundError("no mailbox 'x'", hint="see 'jmap mailbox list'"))
        assert lines == ["jmap: no mailbox 'x'", "hint: see 'jmap mailbox list'"]

    def test_brackets_are_not_markup(self):
        assert reported(UsageError("[bold]x[/bold]")) == ["jmap: [bold]x[/bold]"]

    def test_an_unexpected_error_names_its_type(self):
        assert reported(RuntimeError("boom")) == [
            "jmap: RuntimeError: boom (run with -v for the traceback)"
        ]

    def test_an_error_without_a_message(self):
        assert reported(TransportError()) == ["jmap: TransportError"]

    def test_interrupted(self):
        assert reported(KeyboardInterrupt()) == ["jmap: interrupted"]

    def test_validation_errors_name_the_field(self):
        assert reported(validation_error())[0].startswith("jmap: Input should be")
        with pytest.raises(ValidationError) as caught:
            TypeAdapter(dict[str, int]).validate_python({"n": "x"})
        assert reported(caught.value)[0].startswith("jmap: n: ")

    def test_srv_and_auth_hints(self):
        target = SRVTarget(host="mail.other.net")
        lines = reported(UnconfirmedSRVTargetError("example.com", (target,)))
        assert "--trust-srv mail.other.net" in lines[-1]
        assert "jmap account login" in reported(AuthenticationError("no"))[-1]

    def test_a_request_echoed_as_the_detail_is_not_shown(self):
        echo = '{"using": [], "methodCalls": [["Mailbox/changes", {}, "c1"]]}'
        problem = RequestError("urn:ietf:params:jmap:error:notRequest", status=400, detail=echo)
        assert reported(problem) == [
            "jmap: the server could not read the request "
            "[urn:ietf:params:jmap:error:notRequest; HTTP 400]",
            "hint: an argument may have a value the server cannot read, such as a state or an id",
        ]
        titled = RequestError("about:blank", status=400, title="Bad", detail=echo)
        assert reported(titled) == ["jmap: Bad [about:blank; HTTP 400]"]
        unknown = RequestError("urn:x", status=400, detail=echo)
        assert reported(unknown) == ["jmap: urn:x [urn:x; HTTP 400]"]

    @pytest.mark.parametrize("detail", ["Bad state", "[1]", None])
    def test_any_other_detail_is_shown(self, detail):
        problem = RequestError("urn:ietf:params:jmap:error:notRequest", status=400, detail=detail)
        assert "the server could not read" not in reported(problem)[0]

    def test_invalid_json_from_the_server(self):
        assert reported(DuplicateKeyError("name")) == [
            "jmap: the server's answer is not valid JSON: duplicate object key 'name'",
            "hint: this is a fault in the server; -v shows where it was found",
        ]
        assert exit_code_for(DuplicateKeyError("name")) is ExitCode.REQUEST

    def test_verbose_adds_problem_fields_and_the_traceback(self):
        problem = RequestError("urn:ietf:params:jmap:error:limit", status=400, limit="maxCalls")
        lines = reported(problem, verbose=1)
        assert any("limit=maxCalls" in line for line in lines)
        assert any("RequestError" in line for line in lines[2:])
        method = reported(MethodError("invalidArguments", "c1", {"description": "d"}), verbose=1)
        assert any('"description": "d"' in line for line in method)

    def test_json_mode_ends_with_a_json_error(self):
        problem = RequestError("about:blank", status=503, retry_after=2.0)
        body = json.loads(reported(problem, json_mode=True)[-1])["error"]
        assert body["code"] == 5
        assert body["kind"] == "request"
        assert body["type"] == "about:blank"
        assert body["status"] == 503
        assert body["retryAfter"] == 2.0
        other = json.loads(reported(NotFoundError("x", hint="h"), json_mode=True)[-1])["error"]
        assert other["type"] == "NotFoundError"
        assert other["hint"] == "h"
        assert "status" not in other
