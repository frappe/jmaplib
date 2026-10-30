"""What may reach the server: the read-only mode and dry runs.

Every request and upload the command makes goes through one client, so this is
the one place to stop a change. The library already marks each method that can
change server state (``MethodSpec.mutating``) and decides, per batch, whether
any queued call is one (:func:`jmap.batch.is_mutating`). The guard asks the
same question before anything is sent:

* **Read-only** refuses such a batch, and every upload, before it leaves the
  machine. Reads go on as usual.
* **Dry run** lets reads through, because later steps need what they find, and
  stops at the first batch that would change something. It prints that request
  instead of sending it. An upload pretends to succeed, so a message with
  attachments still shows the request that would create it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jmap.batch import is_mutating
from jmap.blobs import UploadResult
from jmap.cli.errors import CLIError, ExitCode
from jmap.client import JMAPClient
from jmap.core.errors import JMAPError

if TYPE_CHECKING:
    from jmap.batch import Batch
    from jmap.core.ids import Id


class ReadOnlyModeError(CLIError):
    """A change, refused because the account or the run is read-only."""

    code = ExitCode.READ_ONLY


class DryRunDone(JMAPError):  # noqa: N818 - a stop, not a failure
    """The first change of a dry run: what would have been sent.

    ``run`` catches it before any error handling, so it ends the run with exit 0.
    """

    def __init__(self, requests: list[dict[str, Any]], uploads: list[int]) -> None:
        super().__init__("dry run")
        self.requests = requests
        self.uploads = uploads


@dataclass(slots=True)
class Guard:
    """The rules for one run."""

    read_only: bool = False
    dry_run: bool = False
    #: The size of each upload a dry run pretended to make.
    uploads: list[int] = field(default_factory=lambda: [])

    @property
    def writes_allowed(self) -> bool:
        return not (self.read_only or self.dry_run)


class GuardedClient(JMAPClient):
    """A :class:`JMAPClient` that checks the :class:`Guard` before each change."""

    guard: Guard = Guard()

    def execute(self, batch: Batch, *, extra_using: frozenset[str] = frozenset()) -> None:
        if is_mutating(batch, self.capabilities):
            if self.guard.read_only:
                raise ReadOnlyModeError(
                    f"read-only: not sending {', '.join(self._changes(batch))}",
                    hint="this account or run is read-only; reading works as usual",
                )
            if self.guard.dry_run:
                requests = [
                    request.to_wire() for request in batch.requests(extra_using=extra_using)
                ]
                raise DryRunDone(requests, self.guard.uploads)
        super().execute(batch, extra_using=extra_using)

    def upload(
        self,
        content: bytes,
        *,
        content_type: str | None = None,
        account_id: Id | None = None,
    ) -> UploadResult:
        if self.guard.read_only:
            raise ReadOnlyModeError(
                f"read-only: not uploading {len(content)} bytes",
                hint="this account or run is read-only; reading works as usual",
            )
        if self.guard.dry_run:
            self.guard.uploads.append(len(content))
            return UploadResult(
                account_id=account_id or self.default_account,
                blob_id=f"dry-run-blob-{len(self.guard.uploads)}",
                type=content_type or "application/octet-stream",
                size=len(content),
            )
        return super().upload(content, content_type=content_type, account_id=account_id)

    def _changes(self, batch: Batch) -> list[str]:
        """The methods in ``batch`` that would change something, in order, once each."""
        names = [handle.call.name for handle in batch.handles]
        changing = [
            name
            for name in names
            if (spec := self.capabilities.method(name)) is not None and spec.mutating
        ]
        return list(dict.fromkeys(changing))
