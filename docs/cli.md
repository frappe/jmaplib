# Command line

`pip install 'jmaplib[cli]'` adds a `jmap` command. It is a thin layer over the
library: every command is a batch you could write yourself, and `-v` shows each
request it sends. `python -m jmap` runs the same command.

This release has the foundation: accounts, mailboxes, the server's session and
capabilities, and raw method calls. Reading, searching, triage and sending mail
come next, then the shell, contacts, calendars and files.

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
auth = "password"            # password | token
username = ""                # defaults to address
account_id = ""              # a JMAP account in the session, by id or name
experimental = false
trusted_srv_targets = []     # SRV hosts outside the domain you trust
ca_bundle = ""
insecure = false
```

A `password` or `token` key in the file is refused: use `jmap account login`.

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
| 2 | Bad usage: an unknown option, bad JSON, an ambiguous mailbox. |
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
| `jmap account login [NAME]` | Enter the password or token again. |
| `jmap account set-default NAME` | Make it the default. |
| `jmap account remove NAME` | Remove it and its stored secrets. |
| `jmap mailbox list [--tree] [--subscribed]` | Every mailbox, parents first. |
| `jmap mailbox show MAILBOX` | One mailbox: counts, path, your rights. |
| `jmap server session` | The session resource; `--json` prints all of it. |
| `jmap server capabilities` | What the server advertises, and what this build speaks. |
| `jmap server conformance [--markdown]` | Every capability this build models, against the server. |
| `jmap server echo [JSON]` | `Core/echo`. |
| `jmap server refresh` | Fetch the session again. |
| `jmap api call METHOD [JSON]` | Any modelled method, with raw arguments. |

A `MAILBOX` is a role (`inbox`, `sent`, `archive`), a path (`Archive/2025`), a
unique name, or an id. Roles are tried before names, because names are
translated and roles are not. `id:<id>` forces an id.

`jmap api call` takes its arguments as JSON, as `@FILE`, or as `-` for stdin, and
fills in `accountId`. The library's checks still apply: a method this build does
not model, or that the server does not advertise, is refused before it is sent.

```console
$ jmap api call Mailbox/query '{"filter": {"role": "inbox"}}'
```
