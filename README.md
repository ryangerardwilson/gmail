# Gmail CLI

Declarative Gmail-only CLI with multi-account presets.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Google OAuth setup

1. Open Google Cloud Console and create/select a project.
2. Enable the Gmail API for that project.
3. Configure OAuth consent screen:
   - choose `External` (or `Internal` if Workspace-only),
   - add app name/support email,
   - add test users if app is still in testing mode.
4. Create OAuth credentials:
   - `APIs & Services` -> `Credentials` -> `Create Credentials` -> `OAuth client ID`,
   - Application type: `Desktop app`,
   - download the client JSON.
5. Run `gmail auth <client_secret_path>` to create or update a preset and complete browser auth.

Required permissions/scopes used by this CLI:
- `https://www.googleapis.com/auth/gmail.send` (send/reply)
- `https://www.googleapis.com/auth/gmail.readonly` (list/read)
- `https://www.googleapis.com/auth/gmail.modify` (mark read/trash/spam cleanup)

## Binary install (release)

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/gmail/main/install.sh | bash
```

## Config

Default path is `~/.config/gmail/config.json` (or `$XDG_CONFIG_HOME/gmail/config.json`).

Example:

```json
{
  "accounts": {
    "1": {
      "email": "first@yourdomain.com",
      "client_secret_file": "/home/you/.config/gmail/client_secret.json",
      "signature_file": "/home/you/.config/gmail/signatures/account1.txt",
      "spam_senders": ["annoying@promo.biz"],
      "spam_excludes": ["trusted@yourdomain.com"],
      "contacts": {
        "silvia": "xyz@hbc.com"
      }
    },
    "2": {
      "email": "second@yourdomain.com",
      "client_secret_file": "/home/you/.config/gmail/client_secret.json",
      "signature_file": "/home/you/.config/gmail/signatures/account2.txt"
    }
  },
  "defaults": {
    "list_limit": 10,
    "timezone_offset": "+05:30"
  }
}
```

Notes:
- Token files are managed automatically at `~/.local/share/gmail/tokens/<account_key>.json` (or `$XDG_DATA_HOME/gmail/tokens/<account_key>.json`).
- The CLI auto-creates the token data directory when needed.
- Normal app runs only use account-keyed tokens. Legacy preset-number token names are not read implicitly.
- `signature_file` is required for each account and is appended automatically to all outgoing send/reply bodies.
- `defaults.timezone_offset` controls displayed message timestamps in output (`±HH:MM`, for example `+05:30` or `-07:00`).

## Usage

```bash
gmail -h
gmail -v
gmail -u
gmail auth <client_secret_path>
gmail <preset> si
gmail <preset> sc
gmail <preset> sa <spam_email1,spam_email2,...>
gmail <preset> se <email1,email2,...>
gmail <preset> sa -ur
gmail <preset> cn
gmail <preset> cn -a <alias> <email>
gmail <preset> cn -d <alias>
gmail <preset> cn -e
gmail <preset> o <message_id>
gmail <preset> o -t <thread_id>
gmail <preset> mr <message_id>
gmail <preset> mra
gmail <preset> mur <message_id>
gmail <preset> mstr <message_id>
gmail <preset> mustr <message_id>
gmail <preset> d <message_id>
gmail <preset> ms <message_id>
gmail <preset> s -e
gmail <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]
gmail <preset> ls [-o] <query>
gmail <preset> ls [-o] -ur [limit]
gmail <preset> ls [-o] -r [limit]
gmail <preset> ls [-o] -str [limit]
gmail <preset> ls [-o] -ext <limit>
gmail <preset> ls [-o] -snt [limit|query]
gmail <preset> ls -ura [limit]
gmail <preset> ls -ra [limit]
gmail <preset> ls [-o] -t <thread_id>
gmail <preset> r [-a] [-e] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]
gmail <preset> r [-a] [-e] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]
```

Help behavior:
- `gmail -h`: shows full examples.
- `gmail` (no args): shows the same full help as `-h`.

Examples:

```bash
# Send email
gmail auth ~/Documents/credentials/client_secret.json
gmail 1 s -e
gmail 1 s "xyz@example.com" "this is the subject" "this is the body"
gmail 1 s "xyz@example.com" "this is the subject" "this is the body" -cc "cc1@example.com,cc2@example.com" -bcc "audit@example.com"
gmail 1 s "xyz@example.com" "this is the subject" "this is the body" -atch "/tmp/notes.txt"
gmail 1 s "xyz@example.com" "this is the subject" "this is the body" -atch "/tmp/notes.txt" "/tmp/project_dir"

# List and audit messages
gmail 1 ls "from maanas limit 1"
gmail 1 ls "from xyz limit 5"
gmail 1 ls -ur
gmail 1 ls -ur 1
gmail 1 ls -r
gmail 1 ls -r 1
gmail 1 ls -str
gmail 1 ls -str 5
gmail 1 ls -ext 10
gmail 1 ls -snt 10
gmail 1 ls -snt "silvia"
gmail 1 ls -o "from xyz limit 1"
gmail 1 ls -o -ur 1
# Audit unread emails
gmail 1 ls -ura 10
# Audit read emails
gmail 1 ls -ra 10
gmail 1 ls "to silvia limit 1"
gmail 1 ls -t "19ca756c06a7ebcd"

# Single-message utilities
gmail 1 o "18f3abc..."
gmail 1 o -t "19ca756c06a7ebcd"
gmail 1 mr "18f3abc..."
gmail 1 mra
gmail 1 mur "18f3abc..."
gmail 1 mstr "18f3abc..."
gmail 1 mustr "18f3abc..."
gmail 1 d "18f3abc..."
gmail 1 ms "18f3abc..."

# Reply
gmail 1 r "18f3abc..." "Thanks, sharing this now."
gmail 1 r -e "18f3abc..."
gmail 1 r -a "18f3abc..." "Thanks everyone."
gmail 1 r "18f3abc..." "Adding context." -cc "manager@example.com" -bcc "audit@example.com"
gmail 1 r "18f3abc..." "Sharing the latest." -atch "/tmp/project_dir"
gmail 1 r -a "18f3abc..." "Please review." -atch "/tmp/notes.txt" "/tmp/project_dir"
gmail 1 r -t "19ca756c06a7ebcd" "Following up on this thread."
gmail 1 r -t -a "19ca756c06a7ebcd" "Thanks all."

# Spam flow
gmail 1 si
gmail 1 sc
gmail 1 sa "spam1@example.com,spam2@example.com"
gmail 1 sa "@domain1.com,@domain2.com"
gmail 1 se "trusted1@example.com,trusted2@example.com"
gmail 1 se "@trusted-domain.com"
gmail 1 sa -ur

# Contacts
gmail 1 cn
gmail 1 cn -a "silvia" "xyz@hbc.com"
gmail 1 cn -d "silvia"
gmail 1 cn -e
```

Reply flags:
- `-a`: reply-all, keeps original Cc recipients (excluding your own address).
- `-t`: treat target id as `thread_id` instead of `message_id`.
- Use separate flags only (for example: `r -t -a ...` or `r -a -t ...`).
- `-cc`: add comma-separated recipients to Cc for send/reply (trailing option, after required args).
- `-bcc`: add comma-separated recipients to Bcc for send/reply (trailing option, after required args).
- `-atch`: attach one or more file/dir paths; directories are attached as generated `.zip` files (trailing option, after required args).
- `s -e`: open your editor (`$VISUAL`, then `$EDITOR`, else `vim`) with a compose template and send from filled fields.
- `r -e`: open your editor for reply body/CC/BCC/Attachments (target id stays on CLI). Works with separate `-a` and/or `-t` flags.
- Editor template supports `Attachments: "path1,path2,path3"` (comma-separated file/dir paths).

Spam flow commands:
- `si` (spam identify): scans unread non-`@gmail.com` messages and counts sender occurrences, then lists senders with more than 5 unread mails and (on confirm) adds them to `spam_senders`.
- `sc` (spam clean): trashes all messages (read + unread) from `spam_senders`.
- `sa "<spam_email1,spam_email2,...>"`: manually add one or more senders to `spam_senders` (supports full emails and domain rules like `@domain.com`).
- `se "<email1,email2,...>"`: add one or more senders to `spam_excludes` so `si` and `sc` skip them (supports full emails and domain rules like `@domain.com`).
- `sa -ur`: adds senders of all unread messages to `spam_senders` and trashes those unread messages.
- Safety rules for `si`: `@gmail.com` senders and senders from the preset's own domain are never added to `spam_senders`.

Contacts commands:
- `cn` (no args): list contacts for the preset.
- `cn -a <alias> <email>`: add/update a contact alias.
- `cn -d <alias>`: delete a contact alias.
- `cn -e`: open the config file in your editor.
- Contact aliases can be used in `s`/`r` recipient fields (`To`, `-cc`, `-bcc`, and editor template fields).

Bash completion:
- Installer adds Bash completion for the `gmail` command only (not `python main.py`).
- For `gmail <preset> s <TAB>`, completions include that preset's contact aliases.

Declarative query keywords for `ls "<query>"`:
- `from`, `to`, `subject`, `after`, `before`, `unread`, `contains`, `limit`.
- Example: `gmail 1 ls "from xyz limit 5"`.

Message utilities:
- `o <message_id>`: open one message with full body output, mark it as read, and download attachments to current working directory.
- `o -t <thread_id>`: open all messages in a thread (ascending order), apply existing color formatting per message, mark all thread messages as read, and download attachments to current working directory.
- `mr <message_id>`: mark a single message as read.
- `mra`: mark all unread messages as read.
- `mur <message_id>`: mark a single message as unread.
- `mstr <message_id>`: star a single message.
- `mustr <message_id>`: remove star from a single message.
- `d <message_id>`: delete a single message.
- `ms <message_id>`: mark sender as spam (adds sender to `spam_senders` subject to safety normalization) and trashes the message.
- `ls -ur [limit]`: list unread messages only; if `limit` is omitted, uses config default list limit.
- `ls -r [limit]`: list read received messages only (excludes sent); if `limit` is omitted, uses config default list limit.
- `ls -str [limit]`: list starred messages only; if `limit` is omitted, lists all starred messages.
- `ls -ext <limit>`: list external-domain messages only (excludes your own sender address and your preset domain).
- `ls -snt [limit|query]`: list/search sent messages. If `limit` is numeric, it limits sent results. Otherwise it is treated as a sent query.
- `ls -o ...`: prints full body for each listed message and marks listed messages as read.
- `ls -o` is supported with normal list modes (`<query>`, `-ur`, `-r`, `-ext`, `-snt`, `-t`) and not supported with audit modes (`-ura`, `-ra`).
- `ls -ura [limit]`: interactive unread audit. Without `limit`, audits all unread messages continuously in batches of 10. For each unread message: `s` marks spam (adds sender to `spam_senders` and trashes message), `t` trashes message without spam-list update, `n` leaves message unread, `q` stops audit.
- `ls -ra [limit]`: interactive read-mail audit with the same actions as `-ura`; without `limit`, processes read messages continuously in batches of 10.
- Safety rule: both `-ura` and `-ra` never trash emails from `@gmail.com` senders.

## First run auth

On first command for each preset, browser OAuth opens and saves token for that preset.

## Troubleshooting

- `OAuth access is restricted to test users`: add account under OAuth consent screen test users.
- `access_denied`: Workspace admin policy may be blocking this app/scopes.
- `Invalid JSON in config`: remove trailing commas from config file.
