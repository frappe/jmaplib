# Command line

`pip install 'jmaplib[cli]'` adds a `jmap` command. It is a thin layer over the
library: every command is a batch you could write yourself, and `-v` shows each
request it sends. `python -m jmap` runs the same command.

This release has accounts (passwords, tokens or OAuth), mailboxes, reading,
searching, filing and writing mail, an interactive shell, the server's session
and capabilities, and raw method calls. Contacts, calendars and files come next.

## First run

```console
$ jmap account add work --url https://mail.example.com/.well-known/jmap \
    --username alice@example.com
Password for work:
$ jmap mailbox list
$ jmap server capabilities
```

`account add` connects once to check the credential, then stores the password in
the OS keyring and the rest in the configuration file. A `--url` with no path,
such as `https://mail.example.com`, means its `/.well-known/jmap`. Without
`--url`, give `--address` and the server is found from the address (RFC 8620
§2.2). The account's name is only a label: sign in with `--username`, or with
`--address`, which is the username when `--username` is not given.

## Reading mail

```console
$ jmap ls
  #  FLAGS  DATE    FROM    SUBJECT                    SIZE
  1  *      Oct 29  Carol   Re: Invoice for October   12.1K
  2  N@     Oct 28  Bob     Invoice for October       48.0K
1-2 of 312 · Inbox · -date
$ jmap read 2
$ jmap search 'from:bob is:unread after:7d'
$ jmap archive 1-2
```

A listing numbers its rows, and the numbers stay valid in later commands until
the next listing: `jmap read 2`, `jmap move 1-4 Archive/2025`, `jmap flag 1,3`.
That is the *cursor*, kept per account in `$XDG_STATE_HOME/jmap`. Instead of a
number you can give an id, `id:<id>`, `msgid:<message-id>` or `thread:<id>`.

The FLAGS column shows `N` unread, `*` flagged, `@` has attachments, `R`
answered, `F` forwarded, `D` draft and `J` junk.

`jmap ls` lists the inbox; `--in MAILBOX` another, `-A` every mailbox. `jmap
search` looks everywhere and highlights the matches. Both take the same terms,
and options that are shorthand for them:

| Term | Option | Means |
|---|---|---|
| `from:bob` `to:` `cc:` `subject:` `body:` | `--from` `--to` `--subject` | That field contains the text. |
| `invoice` | | Any field contains the word. |
| `is:unread` `is:flagged` `is:answered` `is:draft` | `--unread` `--flagged` | A keyword is set. |
| `tag:work` | | Your own keyword. |
| `has:attachment` | `--attachment` | It has files. |
| `before:2025-01-31` `after:7d` | `--before` `--after` | Dates; also `today`, `yesterday`, `2w`, `3m`, `1y`. |
| `larger:2M` `smaller:10K` | | Size. |
| `in:archive` `notin:junk` | `--in` | Mailboxes. |
| `header:List-Id` | | A header is present, or `header:Name=value` contains a value. |

Terms next to each other must all match. `OR`, `NOT` or a leading `-`, and
parentheses combine them: `from:bob (invoice OR receipt) -has:attachment`. Quote
a whole query, or a value with spaces: `'subject:"weekly report"'`.

`--sort` takes a comma list of `date`, `subject`, `from`, `to`, `size`,
`flagged` and `unread`, each with `-` for descending; the default is `-date`.
`-n` sets the page size, `--page` picks a page, `--all` fetches every page,
`--count` prints only the number of matches, and `--threads` shows one row per
conversation.

`jmap read` prints the headers, the body as text (HTML is converted; `--html`
prefers the HTML part), and the attachments, then marks the message read
(`--no-mark-seen` does not). `--headers List-Id,X-Spam` adds headers, and
`--raw` prints the message source.

Filing commands patch a message, never rewrite it. `jmap move 3 Archive` takes
the message out of the mailbox it was listed from and puts it in Archive; a
message also filed elsewhere stays there. When the source is unknown (you named
it by id), give `--from MAILBOX`, or the message ends up in the target alone.

## Writing mail

```console
$ jmap compose                                   # write it in your editor
$ jmap send --to bob@example.com --subject Lunch --body "One o'clock?"
$ jmap reply 2 --all
$ jmap forward 2 --to carol@example.com --body FYI
$ jmap send --to team@example.com --subject Notes --body-file notes.md --markdown
$ jmap send --to bob@example.com --body "Reminder" --send-at +2h
```

`compose`, `reply` and `forward` open your editor on a draft like this one, on a
terminal and unless the body is given. A file you save unchanged is not sent.

```
# Lines starting with # are ignored. Leave a blank line before the body.
# You can send as: Alice <alice@example.com>, Alice (work) <alice@work.example>
From: Alice <alice@example.com>
To: bob@example.com
Cc:
Bcc:
Subject: Lunch?
Attach: ~/menu.pdf

One o'clock?
```

Other headers you may add: `Reply-To`, `In-Reply-To`, `References`, `Send-At`
(`+30m`, `+2h`, `+1d`, or a date and time), `Content-Type` (`text/markdown` or
`text/html`), `Keywords`, and any `X-` header. `Attach: blob:ID` attaches a blob
the server already holds. A `--body-file` that starts with headers is read the
same way.

Each send is one request: the message is created in Drafts and submitted, and
the server moves it to Sent and clears `$draft` only if the submission
succeeds. A reply's original is marked `$answered` (a forward's `$forwarded`)
in the same request. If the server refuses to send, the message stays in Drafts
and the command exits 8, naming the `jmap draft send` that will retry it. For a
server that files sent mail itself, set `send_move_to_sent = false`.

`--from` picks the identity by address; otherwise a reply goes from the address
the original was sent to, and anything else from the identity matching your
username. `--send-at` needs a server offering FUTURERELEASE, and is checked
against its `maxDelayedSend`; `jmap submission cancel ID` stops a held message.
`jmap send --eml FILE` sends a whole message file as it is.

## Calendars and files

Calendars (draft-ietf-jmap-calendars) and files (draft-ietf-jmap-filenode) are
experimental capabilities. A `calendar`, `event`, `freebusy` or `file` command
turns them on by itself, so you do not need `--experimental`.

```console
$ jmap calendar list
$ jmap event list                                # today and the next 7 days
$ jmap event list --from 2026-12-01 --to +1w --calendar Work
$ jmap event create Lunch --start 2026-11-05T12:30 --duration 45m --alert 15m
$ jmap event create Review --start tomorrow --all-day --rrule "FREQ=WEEKLY;COUNT=4"
$ jmap event create Sync --start 2026-11-05T09:00 --attendee bob@example.com --send-invites
$ jmap event import party.ics
$ jmap freebusy bob@example.com --from today --to +3d
```

`event list` shows each occurrence of a repeating event on its own row. Give
`--no-expand` for one row per event. `--from` takes `today`, `tomorrow`, `+2d`,
or a date and time. `+90m`, `+2h`, `+3d` and `+1w` in `--to` count from `--from`.
Times are read and shown in the zone that `--tz` names. Without `--tz`, the zone
is `$TZ`, then the zone of this computer.

A server expands repeating events only over a limited window
(`maxExpandedQueryDuration`). A longer window is sent as several queries in one
request, and an event in two of them is listed once.

The id of one occurrence is not the id of its event. `event update` and
`event delete` refuse an occurrence id, because a change to it can change the
whole series. Give `--this-occurrence` to change only that occurrence: the change
goes into the event's `recurrenceOverrides`. Give the event's own id to change
every occurrence.

`event create` sends no invitations unless you give `--send-invites`. With
`--attendee`, you are the organizer, as your default scheduling identity.

```console
$ jmap file ls /Projects
$ jmap file tree
$ jmap file upload report.pdf /Projects/
$ jmap file download /Projects/report.pdf --dir ~/Downloads
$ jmap file mkdir -p /Projects/2026/Q4
$ jmap file mv /Projects/report.pdf /Archive
$ jmap file rm -r /Projects/2025
```

A path such as `/Projects/report.pdf` is found one name at a time, so each name
costs one request. `/` is the top level. `upload` to a directory, or to a path
that ends in `/`, keeps the file's name. Any other path gives the file a new
name. A name that is already taken fails unless you give `--replace`. `rm`
deletes a directory only if it is empty, unless you give `-r`.

## The shell

`jmap shell` keeps one connection open and runs commands typed at its prompt,
with history and Tab completion. Everything that works as `jmap <command>` works
as `<command>` here, and a few commands exist only here:

```console
$ jmap shell
alice@work:Inbox> ls
alice@work:Inbox> read 2
alice@work:Inbox> cd Archive/2025
alice@work:Archive/2025> ls --unread
alice@work:Archive/2025> move 1 .
alice@work:Archive/2025> exit
```

| | |
|---|---|
| `cd MAILBOX` | Make it the current mailbox: listings show it, and `.` names it. `cd ..` goes up, `cd` back to the inbox. |
| `pwd` | Print the current mailbox. |
| `use ACCOUNT` | Switch to another named account; `use ACCOUNT:ID` also picks the JMAP account. |
| `refresh` | Fetch the session again. |
| `help [COMMAND]` | Help for a command. |
| `exit`, `quit`, Ctrl-D | Leave. Ctrl-C drops the line you are typing. |

Global options given to `jmap shell` (`-a work`, `--no-keyring`) apply to every
line. History is kept in `$XDG_STATE_HOME/jmap/history`. Without
`prompt_toolkit`, or when lines come from a pipe, the shell reads plain lines,
so a script can be fed to it: `jmap shell < triage.txt`.

## Two kinds of account

A **named account** is one server and one credential. You can have any number;
`-a NAME` picks one, and otherwise the default (`jmap account set-default`).

A **JMAP account** lives inside the server's session: your own mailbox, a shared
one, a delegated calendar. You never configure these; the server lists them.

```console
$ jmap account show
     ID   NAME                PERSONAL   READ-ONLY   PRIMARY FOR
 *   u1   Alice Example       yes        no          core, mail, submission
     u7   Sales               no         yes
```

Without `--account-id`, each call goes to the account the server names as
primary for its capability. `--account-id u7` (or `--account-id Sales`, by
name) sends every call, upload and download to that one.

## Secrets

Secrets are never accepted as an argument, where other users could read them from
the process list. They are looked for in this order:

1. `--password-stdin` or `--token-stdin` (on `account add` and `account login`),
2. `$JMAP_PASSWORD` or `$JMAP_TOKEN`,
3. the OS keyring, under the service `jmaplib`,
4. a prompt, when there is a terminal.

`--no-keyring` neither reads nor writes the keyring. With no keyring backend on
the machine, the command says so once and uses the other sources.

## OAuth

Servers that sign in with OAuth need no password at all:

```console
$ jmap account add home --url https://mail.example.com --auth oauth
Open this address to sign in:
https://auth.example.com/authorize?...
```

The command finds the authorization server from the JMAP server (RFC 9728 and
RFC 8414), registers itself as a client if you give no `--client-id` (RFC 7591),
and opens your browser; after you sign in there, the browser comes back to a
listener on this machine. With `--no-browser` it only prints the address. On a
machine no browser can reach, `--device` prints a code to enter on another
device instead (RFC 8628). `--scope` and `--issuer` override what is asked for
and where.

The token goes to the keyring and renews itself before it expires; when the
server rotates refresh tokens, the new one is saved before the old one is
dropped. `jmap account login NAME` signs in again.

## SRV targets

When `--address` finds the server through DNS and the answer names a host
outside the address's domain, the command asks before sending your credentials
there (RFC 6186 §6). A host you agree to is added to the account's
`trusted_srv_targets`, so you are asked once. Without a terminal it is not
tried; `account add --trust-srv HOST` trusts it in advance.

## Global options

These work on every command, before or after the command name.

| Option | |
|---|---|
| `-a`, `--account NAME` | The named account. |
| `--account-id ID` | The JMAP account in the session, by id or name. |
| `-o`, `--output FORMAT` | `auto`, `table`, `plain`, `json`, `jsonl` or `ids`. |
| `--json`, `--jsonl` | The same as `-o json` and `-o jsonl`. |
| `-q`, `--quiet` | No notes on stderr. |
| `-v`, `--verbose` | One line per request and response; `-vv` adds the HTTP log. |
| `--color WHEN` | `auto`, `always` or `never`. `NO_COLOR` is honoured. |
| `--no-keyring` | Do not use the OS keyring. |
| `--experimental` | Resolve draft capabilities (calendars, files). |
| `--insecure` | Do not verify TLS certificates. |
| `--config FILE` | Another configuration file. |
| `-y`, `--yes` | Answer yes to confirmations. |

A command that needs an experimental capability turns it on by itself, so
`--experimental` is rarely needed.

## Output

On a terminal, results are tables. Elsewhere they are tab-separated lines with no
styling, which `cut` and `awk` can read.

`--json` prints the server's own objects, exactly as the library's `to_wire()`
gives them. A list is a bare JSON array, so `jq '.[].name'` works. Totals and
other notes go to stderr. This JSON is a stable contract: a field the command adds
will start with `_`, and there are none yet.

```console
$ jmap mailbox list --json | jq -r '.[] | select(.unreadEmails > 0) | .name'
```

## Configuration

The file is `$XDG_CONFIG_HOME/jmap/config.toml` (`~/.config/jmap/config.toml`;
`%APPDATA%\jmap\config.toml` on Windows), or `$JMAP_CONFIG`. It is written with
owner-only permissions.

```toml
default_account = "work"

[defaults]
output = "auto"     # auto | table | plain | json | jsonl | ids
color = "auto"      # auto | always | never
pager = "auto"      # auto | never ($JMAP_PAGER, then $PAGER)
timeout = 30.0

[accounts.work]
address = "alice@example.com"
url = "https://mail.example.com/.well-known/jmap"   # omit to discover it
auth = "password"            # password | token | oauth
username = ""                # defaults to address
account_id = ""              # a JMAP account in the session, by id or name
experimental = false
trusted_srv_targets = []     # SRV hosts outside the domain you trust
ca_bundle = ""
insecure = false

[accounts.work.oauth]          # for auth = "oauth"; filled in by `account add`
client_id = ""
scope = ""
issuer = ""
```

A `password` or `token` key in the file is refused: use `jmap account login`.

`jmap config path` prints where the file is, `jmap config edit` opens it in
`$VISUAL`, `$EDITOR` or `defaults.editor` and checks it afterwards, and
`jmap config set KEY VALUE` changes one value: `jmap config set defaults.output
json`, `jmap config set accounts.work.insecure true`. A value is read as TOML
when it is one (`true`, `30`, `["a", "b"]`), and as text otherwise.

For each setting a flag wins over an environment variable, which wins over the
file. The variables are `JMAP_ACCOUNT`, `JMAP_URL`, `JMAP_USER`, `JMAP_PASSWORD`,
`JMAP_TOKEN`, `JMAP_CONFIG`, `JMAP_EXPERIMENTAL`, `JMAP_INSECURE` and
`JMAP_PAGER`. With `JMAP_URL` set and no account named, the command needs no
file at all, which suits CI:

```console
$ JMAP_URL=https://mail.example.com/.well-known/jmap JMAP_USER=ci@example.com \
    JMAP_PASSWORD=... jmap server capabilities
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | An unexpected error; `-v` shows the traceback. |
| 2 | Bad usage: an unknown option, bad JSON or search text, an ambiguous mailbox. |
| 3 | Authentication failed. |
| 4 | No connection: network, TLS, discovery, an untrusted SRV target. |
| 5 | The server refused the whole request (RFC 7807). |
| 6 | A method failed (RFC 8620 §3.6.2). |
| 7 | The server does not offer what the command needs. |
| 8 | A change partly failed. |
| 9 | Not found: no such mailbox, account or id. |
| 10 | The configuration is wrong. |
| 130 | Interrupted. |

Each failure is one `jmap: <message>` line on stderr, sometimes with a `hint:`
line. With `--json`, the last stderr line is also a JSON object:
`{"error": {"code", "kind", "message", "type", "hint", ...}}`.

## Commands

| Command | |
|---|---|
| `jmap account add NAME` | Add a named account and store its secret. |
| `jmap account list` | The named accounts; `*` marks the default. |
| `jmap account show [NAME]` | The JMAP accounts the session lists. |
| `jmap account test [NAME]` | Connect and call `Core/echo`. |
| `jmap account login [NAME]` | Enter the password or token again, or sign in with OAuth again. |
| `jmap account set-default NAME` | Make it the default. |
| `jmap account remove NAME` | Remove it and its stored secrets. |
| `jmap mailbox list [--tree] [--subscribed]` | Every mailbox, parents first. |
| `jmap mailbox show MAILBOX` | One mailbox: counts, path, your rights. |
| `jmap mailbox create PATH [--role ROLE]` | A new mailbox: `Receipts`, `Archive/2026`. |
| `jmap mailbox rename MAILBOX NAME` | Rename it in place. |
| `jmap mailbox move MAILBOX [PARENT]` | Move it under another, or to the top. |
| `jmap mailbox delete MAILBOX [--with-mail]` | Delete it, after you confirm. |
| `jmap mailbox subscribe/unsubscribe MAILBOX` | Show or hide it in mail clients. |
| `jmap mailbox empty MAILBOX [--match QUERY]` | Destroy its mail, after you confirm. |
| `jmap mail list [QUERY]` (`jmap ls`) | Messages in the inbox or `--in MAILBOX`, numbered. |
| `jmap mail search QUERY` (`jmap search`) | Messages anywhere, with matches highlighted. |
| `jmap mail read MSG...` (`jmap read`) | Headers, body and attachments; marks them read. |
| `jmap mail thread MSG` (`jmap thread`) | The conversation, oldest first; `--read` shows it all. |
| `jmap mail move MSG... MAILBOX` (`jmap move`) | File messages elsewhere. |
| `jmap mail copy MSG... MAILBOX` | Add them to another mailbox too. |
| `jmap mail archive MSG...` (`jmap archive`) | Move them to the archive mailbox. |
| `jmap mail delete MSG... [--permanent]` (`jmap delete`) | Move them to Trash, or destroy them. |
| `jmap mail flag/unflag MSG...` (`jmap flag`) | Set or clear the flag. |
| `jmap mail seen/unseen MSG...` (`jmap seen`) | Mark them read or unread. |
| `jmap mail junk/notjunk MSG...` | Mark them junk and file them in Junk, or the reverse. |
| `jmap mail keyword MSG... --add K --remove K` | Set or clear any keyword. |
| `jmap server session` | The session resource; `--json` prints all of it. |
| `jmap server capabilities` | What the server advertises, and what this build speaks. |
| `jmap server conformance [--markdown]` | Every capability this build models, against the server. |
| `jmap server echo [JSON]` | `Core/echo`. |
| `jmap server refresh` | Fetch the session again. |
| `jmap compose` | Write a message in your editor and send it; `--draft` saves it. |
| `jmap send --to ADDR ...` | Send without an editor; `--eml FILE` sends a message file. |
| `jmap reply MSG [--all] [--no-quote]` | Reply, quoting the original; marks it answered. |
| `jmap forward MSG --to ADDR [--attach-original]` | Forward with its attachments; marks it forwarded. |
| `jmap draft list/new/edit/send/delete` | Drafts: list them, write one, edit and replace, send, delete. |
| `jmap attachment list MSG` | A message's attachments, numbered. |
| `jmap attachment download MSG [N/NAME...] [--all] [--dir DIR] [--save FILE]` | Save them; `--save -` writes to stdout. |
| `jmap identity list/show/create/update/delete` | The addresses you send from, and their signatures. |
| `jmap submission list [--pending] / show / cancel ID` | Sent and held messages; cancel one that has not gone. |
| `jmap vacation show/set/enable/disable` | The automatic reply while you are away. |
| `jmap mail import FILE [--to MAILBOX]` | Add a message file to a mailbox without sending it. |
| `jmap mail export MSG... [--dir DIR] [--save FILE] [--mbox FILE]` | Save messages as `.eml` files or one mbox. |
| `jmap calendar list/create/rename/delete` | Calendars; `delete --with-events` also deletes their events. |
| `jmap event list [--from --to --calendar --tz --no-expand]` | Events in a window, each occurrence on its own row. |
| `jmap event show/update/delete EVENT [--this-occurrence]` | One event; change or delete it, or one occurrence. |
| `jmap event create TITLE --start WHEN [--rrule --alert --attendee --send-invites]` | A new event, one hour long unless you say otherwise. |
| `jmap event import FILE.ics [--calendar CAL]` | Add the events in an iCalendar file. |
| `jmap event notifications [--clear]` | What others changed in events you can see. |
| `jmap freebusy WHO [--from --to --details]` | When a person or resource is busy. |
| `jmap file ls/tree [PATH]` | Files and directories on the server. |
| `jmap file upload FILE [PATH] / download PATH` | Copy a file to or from the server. |
| `jmap file mkdir [-p] / rm [-r] / mv` | Create, delete, move and rename. |
| `jmap api call METHOD [JSON]` | Any modelled method, with raw arguments. |
| `jmap shell` | Run commands on one connection, with `cd`, history and completion. |
| `jmap config path` / `edit` / `set KEY VALUE` | The configuration file. |
| `jmap --install-completion` | Tab completion for your shell (bash, zsh, fish, PowerShell). |

A `MAILBOX` is a role (`inbox`, `sent`, `archive`), a path (`Archive/2025`), a
unique name, or an id. Roles are tried before names, because names are
translated and roles are not. `id:<id>` forces an id.

`jmap api call` takes its arguments as JSON, as `@FILE`, or as `-` for stdin, and
fills in `accountId`. The library's checks still apply: a method this build does
not model, or that the server does not advertise, is refused before it is sent.

```console
$ jmap api call Mailbox/query '{"filter": {"role": "inbox"}}'
```
