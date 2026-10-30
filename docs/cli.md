# Command line

`pip install 'jmaplib[cli]'` adds a `jmap` command. It is a thin layer over the
library: every command is a batch you could write yourself, and `-v` shows each
request it sends. `python -m jmap` runs the same command, and so does
`jmap-cli`.

macOS ships a Java tool at `/usr/bin/jmap`. If `jmap --version` does not print
`jmap` and a version number, that tool comes first in your `PATH`. Use
`jmap-cli`, or put the directory pip installs to (for example `~/.local/bin`)
before `/usr/bin` in `PATH`.

It covers accounts (passwords, tokens or OAuth), mailboxes, reading, searching,
filing and writing mail, contacts, calendars, files, sharing, read receipts,
blobs, Sieve filters, quotas, changes and push, an interactive shell, the
server's session and capabilities, and raw method calls.

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
conversation. `--anchor MSG` starts the page at a message instead of a page
number, and `--anchor-offset N` moves the start N messages later (or earlier,
when N is negative). The server tells the listing where that is, so the numbers
stay right.

`--columns` picks the columns and their order from `flags`, `date`, `from`,
`to`, `subject`, `preview`, `size`, `id` and `thread`:

```console
$ jmap ls --columns date,from,subject,preview
```

`jmap read` prints the headers, the body as text (HTML is converted; `--html`
prefers the HTML part), and the attachments, then marks the message read
(`--no-mark-seen` does not). `--headers List-Id,X-Spam` adds headers, and
`--raw` prints the message source. `--part ID` prints the bytes of one part of
the message, by its part id; a wrong id lists the message's parts. `--save FILE`
writes `--raw` or `--part` output to a file instead.

Filing commands patch a message, never rewrite it. `jmap move 3 Archive` takes
the message out of the mailbox it was listed from and puts it in Archive; a
message also filed elsewhere stays there. When the source is unknown (you named
it by id), give `--from MAILBOX`, or the message ends up in the target alone.

`jmap mail copy MSG... MAILBOX` adds messages to another mailbox. With
`--to-account ACCOUNT`, it copies them into a mailbox of another JMAP account in
the same session (by id or name, as `jmap account show` lists them), with
`Email/copy`. The originals stay where they are.

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

## Contacts

```console
$ jmap addressbook list
$ jmap contact list                               # every card
$ jmap contact list ada --book Work --sort surname
$ jmap contact show ada@example.com
$ jmap contact create --given Grace --surname Hopper --email grace@example.com --org Navy
$ jmap contact edit ada@example.com               # the card's JSON, in your editor
$ jmap contact import people.vcf --book Work
$ jmap contact delete c42
```

Contacts use RFC 9610: cards are JSContact `ContactCard` objects, and each
card is in one or more address books. A `CONTACT` is a number from the last
`contact list`, a card id, or an email address that exactly one card has. An `--book` is an address book's id or
name. Without `--book`, `create` and `import` use the default address book.

`contact list` sends `ContactCard/query` and `ContactCard/get` in one request.
`TEXT` matches any text in the card. `--sort` takes `surname`, `given`,
`created` or `updated`, and a leading `-` reverses the order. RFC 9610 makes
servers support only `created` and `updated`, so a server can refuse the
others. `--json` prints the cards as the server sends them.

`contact edit` opens the card's JSON in your editor. When you save, each
top-level property you changed is sent whole, and each one you removed is
set to `null`. The `id` cannot change. If the file is not a JSON object, you
can edit it again.

`contact import` reads a vCard file, or `-` for stdin. The server converts
vCard to JSContact with `ContactCard/parse`, which needs
`urn:ietf:params:jmap:contacts:parse` (Stalwart offers it). This method reads
one card per blob, so the command uploads each card in the file as its own
blob. Then it parses them and creates the cards. The server limits how many
blobs one parse can take, but does not say what the limit is. If the server
answers `requestTooLarge`, the command sends half as many and tries again. It
does the same for the create. A card the server cannot read is named on
stderr, and the command exits 8.

`addressbook delete` refuses an address book that holds cards, unless you give
`--with-contacts`. Then the server destroys the cards too.

Fastmail and older Cyrus servers have their own `Contact` type from before
RFC 9610. On those servers, `contact list`, `show`, `edit` and `delete` work,
and the tables show the fields those servers are known to send. `--book` and
`--sort` need RFC 9610 address books, and so do `create`, `import` and
`addressbook`. These exit 7 on such a server.

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

## Sharing and read receipts

A *principal* is a person, group or resource on the server (RFC 9670). You
share a mailbox, calendar, address book or file with a principal.

```console
$ jmap principal list bob
$ jmap share grant mailbox:Team bob@example.com --write
$ jmap share list mailbox:Team
$ jmap share revoke mailbox:Team bob@example.com
$ jmap share notifications
```

The object is `mailbox:MAILBOX`, `calendar:ID`, `addressbook:ID` or `file:ID`.
A mailbox can be a role, path, name or id, as everywhere else. Give the
principal by id, address or name. `jmap principal me` shows your own.

`--read`, `--write` and `--admin` each give a set of rights, and each set
includes the one before it. `--admin` also lets the principal delete the object
and share it. `--rights readItems,addItems` gives exactly the rights you name,
with or without the `may` prefix. A grant replaces what that principal had.

Each change is a patch on one principal's entry in `shareWith`, so other
principals keep their access. `share unshare` takes access away from everyone,
after you confirm. You cannot grant rights to the owner of the account: the
owner has every right already.

`share list` shows the mailboxes you share, and `share list calendar` the
calendars. `share subscribe` and `share unsubscribe` show or hide something
another person shared with you. `share notifications` lists the changes other
people made to your access, newest first, and `--clear` deletes them.

`jmap mdn send MSG` sends a read receipt (RFC 9007) and marks the message
`$mdnsent`. A message that has the keyword already gets no second receipt.
`--type` is `displayed` (the default), `deleted`, `dispatched` or `processed`.
`--auto` says a program sent it, and `--include-original` quotes the message
back to its sender. `jmap mdn parse MSG` shows what a receipt you got says. It
also takes `blob:ID`.

## Server tools

```console
$ jmap quota                                     # used, and the limit
$ jmap sieve put filters.sieve --activate
$ jmap blob upload report.pdf
$ jmap changes Email --since 7a3f --fetch
$ jmap watch --new-mail --match from:boss
$ jmap api batch calls.json
```

### Blobs

A blob is binary data with an id: an attachment, a message source, a Sieve
script. `jmap blob upload FILE` prints the new blob's id, and `jmap blob
download ID` writes its bytes to stdout. On a terminal, `download` needs
`--save FILE`. The other three verbs are method calls:

- `jmap blob info ID... [--digest sha-256]` shows the size and the digests the
  server offers (RFC 9404).
- `jmap blob lookup ID... [--type Email]` shows which objects refer to each
  blob. Without `--type`, it asks about every type the server can search.
- `jmap blob copy ID... --from-account ID` copies blobs from another JMAP
  account into the selected one.

### Sieve filters

`jmap sieve put FILE` uploads the script, checks it with
`SieveScript/validate`, and stores it under the file's name, or `--name`. A
script of the same name is replaced. `--activate` makes it the active script
in the same request. A script the server does not accept is not stored, and
the command exits 2 with the server's explanation.

`jmap sieve validate FILE` only checks. `get` prints a script, and `activate`,
`deactivate` and `delete` change which script runs. To delete the active
script, `delete` deactivates it first, in the same request.

### Quotas

`jmap quota` lists each quota with what is used and the hard limit. `jmap quota
show QUOTA` adds the warning and soft limits. The server returns only quotas
for capabilities named in the request, so the request names every capability
this build shares with the server.

### Changes

Every JMAP data type has a state string. `jmap changes TYPE` prints the current
one. Keep it, and later `jmap changes TYPE --since STATE` prints the ids
created, updated and destroyed since then, and the new state on stderr. The
command follows every page the server sends. `--max N` sets the page size, and
`--fetch` also gets the created and updated objects. With `--json`, the output
is one `Foo/changes` response that holds every page, or with `--fetch` the
objects. A state the server can no longer compare against exits 10: get the
current state and start again.

### Watching for changes

`jmap watch` holds the server's event source open and prints a line for each
type that changes, until you press Ctrl-C:

```console
$ jmap watch --types Email,Mailbox
u7	Email	s1240
u7	Mailbox	s88
```

`--ping 30` asks the server for a keep-alive this often, so a dead connection
is found. A connection that drops is opened again, and resumes where it
stopped. Because the output is a stream, `--json` prints one object per line,
as `--jsonl` does.

`--new-mail` prints one line for each message that arrives, with its id, date,
sender and subject. A message that is a draft, or is already read (such as the
copy of a message you sent), is not new mail. `--match QUERY` keeps only the
messages a search finds, in the language of `jmap search`.

`watch` listens on the server's event source. If a proxy in front of the server
holds that stream back, nothing arrives, not even the keep-alive ping; after the
ping deadline `watch` says so once. `--poll SECONDS` then asks for each type's
state that often instead, over the plain API, and everything else works the
same:

```console
$ jmap watch --poll 30 --new-mail
```

`--exec CMD` runs a command for each change, with the event as JSON on its
stdin. Its environment also has `JMAP_ACCOUNT` (the named account, so a `jmap`
command in it uses the same account), `JMAP_TYPES` (the types that changed,
comma-separated) and `JMAP_STATES` (the new states, as JSON). The command runs
through your shell, so pipes and variables work: `--exec 'jq .changed | tee -a
changes.log'`.

### Push subscriptions

A push subscription makes the server POST each change to a URL you own,
instead of keeping a connection open:

1. Run `jmap push add --url https://push.example.com/hook --types Email`.
2. Read the verification code that the server POSTs to the URL.
3. Run `jmap push verify ID CODE`. The server sends nothing else before this.

`--expires +7d` (or `12h`, or a UTC date) asks for an end date, and `jmap push
renew ID` moves it. The server can choose an earlier date. `jmap push list` and
`remove` show and delete subscriptions. A subscription belongs to the
credentials that made it, so `list` shows only those.

`--encrypt` makes a key pair and gives the server the public half, so only you
can read what the server pushes (RFC 8291). The private half is kept in the
keyring under the subscription's id. This needs the `jmaplib[push]` extra.
`jmap push decode FILE --subscription ID` decrypts a saved POST body and prints
it as JSON. Without `--subscription`, it reads an unencrypted body. `jmap push
keys` prints the server's VAPID key (RFC 9749), for a browser push service.

### Many calls in one request

`jmap api batch FILE` sends the calls in a JSON file, or `-` for stdin, as one
request. Each call is `[method, arguments, callId]`, and the call id can be
left out. An argument whose name starts with `#` takes its value from an
earlier call's result, named by its call id in the file:

```json
[
  ["Email/query", {"filter": {"from": "bob"}, "limit": 5}, "q"],
  ["Email/get", {"#ids": {"resultOf": "q", "path": "/ids"}, "properties": ["subject"]}, "g"]
]
```

The output is the array of responses, `[method, arguments, callId]` each. A
call that fails appears as `["error", {...}, callId]`, and the command then
exits 6.

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

## Tab completion

`jmap completion SHELL` prints the completion script for `bash`, `zsh`, `fish`
or `powershell`, and `--install` adds it to that shell's startup file. Then Tab
completes commands and options, and also values: account names after `-a`,
mailboxes after `--in`, `--from`, `--to` and in `move`, `copy` and `mailbox`
commands, and the items of `--columns` and `--sort`.

```console
$ jmap completion zsh --install
$ jmap ls --in Arch⇥
$ jmap move 3 Arch⇥
```

Or put it in the startup file yourself: `eval "$(jmap completion zsh)"` in
`~/.zshrc` (`bash` in `~/.bashrc`), or for fish
`jmap completion fish > ~/.config/fish/completions/jmap.fish`. Run it as
`jmap-cli` and the script completes `jmap-cli`.

Mailbox names come from the server. If it does not answer within three seconds,
or needs a password the keyring does not have, Tab offers the roles alone
(`inbox`, `sent`, `archive` and so on): completion never asks you anything.

## AI agents and scripts

An agent that can run commands uses `jmap` as a script does: `--json` prints the
server's own objects, `--count` the number of matches, and the exit code says
what failed. It acts as you, with your saved password, so three things keep it
safe.

- **Read-only.** `--read-only`, `JMAP_READ_ONLY=1`, or `read_only = true` in
  the configuration (for one account, or under `[defaults]` for all) refuses
  every change before it leaves your machine, with exit code 11. Reading works
  as usual, and `read` does not mark mail as read. Each of these can switch
  the mode on; none can switch off what another set. So `read_only = true` in
  the file holds whatever the agent types.
- **Dry run.** `--dry-run` sends the reads a command needs, and stops at the
  first change: it prints that request as JSON and changes nothing. Uploads
  are not made, so a message with attachments still shows the request that
  would create it. An agent can show you the change and wait for your yes.
- **The agent guide.** `jmap agent-guide` prints one page for an agent: the
  safety rules, which command answers which question, the search language and
  the exit codes. `jmap agent-guide --install` saves it as a Claude Code skill
  in `~/.claude/skills/jmap/SKILL.md`, and Claude Code loads it when you ask
  about your mail.

```console
$ jmap --read-only ls --from github --count
$ jmap --dry-run delete 3 --permanent
$ jmap agent-guide --install
```

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
| `--read-only` | Refuse anything that would change the server; reading works. |
| `--dry-run` | Print the first change as JSON instead of sending it. |

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
read_only = false   # true: no account may change anything

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
read_only = false            # true: this account may not change anything

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
| 11 | Refused: the account or the run is read-only. |
| 130 | Interrupted. |

Each failure is one `jmap: <message>` line on stderr, sometimes with a `hint:`
line. With `--json`, the last stderr line is also a JSON object:
`{"error": {"code", "kind", "message", "type", "hint", ...}}`.

## Commands

The [command reference](cli-reference.md) gives every argument and option.

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
| `jmap mail copy MSG... MAILBOX [--to-account ACCOUNT]` | Add them to another mailbox too, or copy them to another JMAP account. |
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
| `jmap addressbook list` | The address books; the default one is marked. |
| `jmap addressbook create NAME [--description TEXT] [--default]` | A new address book. |
| `jmap addressbook rename BOOK NAME` | Rename it. |
| `jmap addressbook delete BOOK [--with-contacts]` | Delete it, after you confirm. |
| `jmap contact list [TEXT] [--book BOOK] [--sort KEY] [-n N]` | Contact cards, or those that match. |
| `jmap contact show CONTACT` | One card: addresses, numbers, notes, address books. |
| `jmap contact create --name/--given/--surname --email --phone --org ...` | A new card. |
| `jmap contact edit CONTACT` | Edit the card's JSON; changed properties are sent. |
| `jmap contact delete CONTACT...` | Delete cards, after you confirm. |
| `jmap contact import FILE [--book BOOK]` | Add the cards in a vCard file. |
| `jmap principal list [TEXT] [--type TYPE]` | People, groups and resources on the server. |
| `jmap principal show PRINCIPAL` / `me` | One principal, or your own. |
| `jmap share grant OBJECT PRINCIPAL --read/--write/--admin/--rights R,R` | Give a principal access. |
| `jmap share revoke OBJECT PRINCIPAL` | Take one principal's access away. |
| `jmap share unshare OBJECT` | Take everyone's access away, after you confirm. |
| `jmap share list [KIND/OBJECT]` | What you share, or who has access to one object. |
| `jmap share subscribe/unsubscribe OBJECT` | Show or hide something shared with you. |
| `jmap share notifications [--clear]` | Changes others made to your access. |
| `jmap mdn send MSG... [--type TYPE] [--auto]` | Send read receipts; marks them `$mdnsent`. |
| `jmap mdn parse MSG.../blob:ID...` | What received read receipts say. |
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
| `jmap api batch FILE` | Several calls in one request, with back-references. |
| `jmap blob upload/download/info/lookup/copy` | Blobs: upload a file, save one, sizes and digests, what refers to it, copy between accounts. |
| `jmap sieve list/get/put/validate/activate/deactivate/delete` | Server-side mail filters. |
| `jmap quota list/show` (`jmap quota`) | What is used against each limit. |
| `jmap changes TYPE [--since STATE] [--fetch]` | The current state, or what changed since one. |
| `jmap watch [--new-mail] [--exec CMD] [--poll SECONDS]` | Follow changes as the server announces them. |
| `jmap push list/add/verify/renew/remove/keys/decode` | Push subscriptions to a URL of yours. |
| `jmap shell` | Run commands on one connection, with `cd`, history and completion. |
| `jmap config path` / `edit` / `set KEY VALUE` | The configuration file. |
| `jmap completion SHELL [--install]` | Tab completion for bash, zsh, fish or PowerShell. |
| `jmap agent-guide [--install]` | The guide for AI agents; `--install` saves it as a Claude Code skill. |

A `MAILBOX` is a role (`inbox`, `sent`, `archive`), a path (`Archive/2025`), a
unique name, or an id. Roles are tried before names, because names are
translated and roles are not. `id:<id>` forces an id.

`jmap api call` takes its arguments as JSON, as `@FILE`, or as `-` for stdin, and
fills in `accountId`. The library's checks still apply: a method this build does
not model, or that the server does not advertise, is refused before it is sent.

```console
$ jmap api call Mailbox/query '{"filter": {"role": "inbox"}}'
```
