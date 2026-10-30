"""``jmap share``: who else may use a mailbox, calendar, address book or file (RFC 9670).

Every change is a patch on one principal's entry in ``shareWith``, never the
whole map, so granting Bob access does not revoke Carol's. Calendars, address
books and files are named by id and changed with ``Foo/set`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Final

import typer

from jmap import sharing
from jmap.capabilities.calendars import CALENDARS_URN
from jmap.capabilities.contacts import CONTACTS_URN
from jmap.capabilities.files import FILENODE_URN
from jmap.capabilities.mail import MAIL_URN
from jmap.cli.commands import cli, report_set
from jmap.cli.commands.principal import Lookup, principal_account
from jmap.cli.errors import UsageError
from jmap.cli.ids import MailboxIndex
from jmap.core.ids import Id

if TYPE_CHECKING:
    from jmap.cli.context import CLIContext
    from jmap.client import JMAPClient

app: typer.Typer = typer.Typer(no_args_is_help=True)

Object = Annotated[
    str,
    typer.Argument(
        metavar="OBJECT", help="mailbox:MAILBOX, calendar:ID, addressbook:ID or file:ID."
    ),
]
Who = Annotated[str, typer.Argument(help="The principal, by id, address or name.")]


@dataclass(frozen=True, slots=True)
class Kind:
    """A data type that can be shared, and its rights from least to most."""

    name: str
    type_name: str
    urn: str
    #: What ``--read`` gives; ``--write`` and ``--admin`` each add to the one before.
    read: tuple[str, ...]
    write: tuple[str, ...]
    admin: tuple[str, ...]

    @property
    def rights(self) -> tuple[str, ...]:
        return (*self.read, *self.write, *self.admin)

    def preset(self, level: str) -> dict[str, bool]:
        """Every right of this type, held up to ``level``: read, write or admin."""
        held = {"read": self.read, "write": (*self.read, *self.write), "admin": self.rights}
        return {name: name in held[level] for name in self.rights}

    def named(self, words: str) -> dict[str, bool]:
        """Every right of this type, held when named: ``readItems`` or ``mayReadItems``."""
        known = {name.lower(): name for name in self.rights}
        known |= {name.removeprefix("may").lower(): name for name in self.rights}
        chosen = dict.fromkeys(self.rights, False)
        for word in filter(None, (part.strip() for part in words.split(","))):
            name = known.get(word.lower()) or (word if word.startswith("may") else None)
            if name is None:
                raise UsageError(
                    f"a {self.name} has no right {word!r}", hint=", ".join(self.rights)
                )
            chosen[name] = True
        return chosen


KINDS: Final = {
    kind.name: kind
    for kind in (
        Kind(
            "mailbox",
            "Mailbox",
            MAIL_URN,
            read=("mayReadItems", "maySetSeen"),
            write=("mayAddItems", "mayRemoveItems", "maySetKeywords", "mayCreateChild"),
            admin=("mayRename", "mayDelete", "maySubmit", "mayShare"),
        ),
        Kind(
            "calendar",
            "Calendar",
            CALENDARS_URN,
            read=("mayReadFreeBusy", "mayReadItems"),
            write=("mayWriteAll", "mayWriteOwn", "mayUpdatePrivate", "mayRSVP"),
            admin=("mayShare", "mayDelete"),
        ),
        Kind(
            "addressbook",
            "AddressBook",
            CONTACTS_URN,
            read=("mayRead",),
            write=("mayWrite",),
            admin=("mayShare", "mayDelete"),
        ),
        Kind(
            "file",
            "FileNode",
            FILENODE_URN,
            read=("mayRead",),
            write=("mayAddChildren", "mayRename", "mayModifyContent", "mayDelete"),
            admin=("mayShare",),
        ),
    )
}


@dataclass(frozen=True, slots=True)
class Target:
    """``mailbox:Inbox``: a kind, and the object of that kind, if one is named."""

    text: str
    kind: Kind
    reference: str | None

    @classmethod
    def parse(cls, text: str, *, whole_kind: bool = False) -> Target:
        name, colon, reference = text.partition(":")
        kind = KINDS.get(name.lower())
        if kind is None or (colon and not reference):
            raise UsageError(f"{text!r} names nothing to share", hint="mailbox:Inbox, calendar:ID")
        if not colon and not whole_kind:
            raise UsageError(f"which {kind.name}?", hint=f"{kind.name}:ID")
        return cls(text, kind, reference or None)

    def object_id(self, context: CLIContext) -> str:
        """The object's id; a mailbox may also be a role, path or name."""
        reference = str(self.reference)
        return str(context.mailbox(reference)["id"]) if self.kind.name == "mailbox" else reference


@dataclass(frozen=True, slots=True)
class Accounts:
    """Where the object lives, where its principals live, and who owns it."""

    data: str | None
    principals: Id | None
    owner: str | None

    @classmethod
    def of(cls, client: JMAPClient, kind: Kind) -> Accounts:
        data = client.session.capability_account(kind.urn, client.default_account)
        owner = sharing.owner_of(client.session, data) if data else None
        return cls(data, principal_account(client, data), owner)


@app.command()
def grant(
    ctx: typer.Context,
    target: Object,
    principal: Who,
    read: Annotated[bool, typer.Option("--read", help="Read it.")] = False,
    write: Annotated[bool, typer.Option("--write", help="Read and change it.")] = False,
    admin: Annotated[bool, typer.Option("--admin", help="Also delete and share it.")] = False,
    rights: Annotated[
        str | None, typer.Option(help="Exactly these rights: readItems,addItems.")
    ] = None,
) -> None:
    """Give a principal access, replacing what it had; others keep theirs."""
    context = cli(ctx)
    found = Target.parse(target)
    chosen = _rights(found.kind, read=read, write=write, admin=admin, rights=rights)
    object_id, who, accounts = _resolve(context, found, principal)
    try:
        patch = sharing.grant(who, chosen, owner_principal_id=accounts.owner)
    except sharing.OwnerInShareMapError as error:
        raise UsageError(f"{principal} owns {target}, so already has every right") from error
    held = ", ".join(sorted(sharing.held(chosen))) or "no rights"
    _update(context, found, {object_id: patch}, f"{principal} now has {held} on {target}")


@app.command()
def revoke(ctx: typer.Context, target: Object, principal: Who) -> None:
    """Take a principal's access away; others keep theirs."""
    context = cli(ctx)
    found = Target.parse(target)
    object_id, who, _ = _resolve(context, found, principal)
    _update(context, found, {object_id: sharing.revoke(who)}, f"{principal} lost {target}")


@app.command()
def unshare(ctx: typer.Context, target: Object) -> None:
    """Take everyone's access away, after you confirm."""
    context = cli(ctx)
    found = Target.parse(target)
    object_id = found.object_id(context)
    context.confirm(f"Stop sharing {target} with everyone?")
    _update(context, found, {object_id: sharing.unshare()}, f"{target} is shared with nobody")


@app.command()
def subscribe(ctx: typer.Context, target: Object) -> None:
    """Show something shared with you in your lists."""
    _subscribe(cli(ctx), target, subscribed=True)


@app.command()
def unsubscribe(ctx: typer.Context, target: Object) -> None:
    """Hide something shared with you; your access stays."""
    _subscribe(cli(ctx), target, subscribed=False)


def _subscribe(context: CLIContext, target: str, *, subscribed: bool) -> None:
    found = Target.parse(target)
    object_id = found.object_id(context)
    done = f"{'subscribed to' if subscribed else 'unsubscribed from'} {target}"
    _update(context, found, {object_id: sharing.subscribe(subscribed=subscribed)}, done)


def _rights(
    kind: Kind, *, read: bool, write: bool, admin: bool, rights: str | None
) -> dict[str, bool]:
    levels = [name for name, given in (("read", read), ("write", write), ("admin", admin)) if given]
    if len(levels) + (rights is not None) != 1:
        raise UsageError("give one of --read, --write, --admin or --rights")
    return kind.named(rights) if rights is not None else kind.preset(levels[0])


def _resolve(context: CLIContext, target: Target, principal: str) -> tuple[str, str, Accounts]:
    """The object's id, the principal's id and the accounts, in one request at most."""
    client = context.require(f"{target.kind.type_name}/set")
    accounts = Accounts.of(client, target.kind)
    load_boxes = target.kind.name == "mailbox" and context.loaded_mailboxes is None
    with client.batch() as batch:
        boxes = MailboxIndex.queue(batch) if load_boxes else None
        lookup = Lookup.queue(client, batch, principal, accounts.principals)
    if boxes is not None:
        context.adopt_mailboxes(MailboxIndex.from_handle(boxes))
    return target.object_id(context), str(lookup.result()["id"]), accounts


def _update(context: CLIContext, target: Target, update: dict[str, Any], done: str) -> None:
    method = f"{target.kind.type_name}/set"
    client = context.require(method)
    with client.batch() as batch:
        handle = batch.add(method, {"update": update})
    if target.kind.name == "mailbox":
        context.forget_mailboxes()
    report_set(context, handle.result, done)
