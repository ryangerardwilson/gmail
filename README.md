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
5. Put that JSON path into each account's `client_secret_file` in config.
6. Run any command (for example `gmail 1 ls -ur 1`) to trigger first-time browser auth.

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
      "spam_senders": ["annoying@promo.biz"]
    },
    "2": {
      "email": "second@yourdomain.com",
      "client_secret_file": "/home/you/.config/gmail/client_secret.json",
      "signature_file": "/home/you/.config/gmail/signatures/account2.txt"
    }
  },
  "defaults": {
    "list_limit": 10
  }
}
```

Notes:
- Token files are managed automatically at `~/.gmail/tokens/<preset>.json`.
- The CLI auto-creates `~/.gmail/` and `~/.gmail/tokens/`.
- `signature_file` is required for each account and is appended automatically to all outgoing send/reply bodies.

## Usage

```bash
gmail -v
gmail -u
gmail <preset> si
gmail <preset> sc
gmail <preset> sa <spam_email1,spam_email2,...>
gmail <preset> sa -ur
gmail <preset> mr <message_id>
gmail <preset> d <message_id>
gmail <preset> s -v
gmail <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]
gmail <preset> ls <query>
gmail <preset> ls -ur [limit]
gmail <preset> ls -ura [limit]
gmail <preset> ls -ra [limit]
gmail <preset> ls -t <thread_id>
gmail <preset> r [-a] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]
gmail <preset> r [-a] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]
```

Examples:

```bash
# Send email
gmail 1 s -v
gmail 1 s "xyz@example.com" "this is the subject" "this is the body"
gmail 1 s "xyz@example.com" "this is the subject" "this is the body" -cc "cc1@example.com,cc2@example.com" -bcc "audit@example.com"
gmail 1 s "xyz@example.com" "this is the subject" "this is the body" -atch "/tmp/notes.txt"
gmail 1 s "xyz@example.com" "this is the subject" "this is the body" -atch "/tmp/notes.txt" "/tmp/project_dir"

# List and audit messages
gmail 1 ls "from maanas limit 1"
gmail 1 ls -ur
gmail 1 ls -ur 1
# Audit unread emails
gmail 1 ls -ura 10
# Audit read emails
gmail 1 ls -ra 10
gmail 1 ls "to silvia limit 1"
gmail 1 ls -t "19ca756c06a7ebcd"

# Single-message utilities
gmail 1 mr "18f3abc..."
gmail 1 d "18f3abc..."

# Reply
gmail 1 r "18f3abc..." "Thanks, sharing this now."
gmail 1 r -a "18f3abc..." "Thanks everyone."
gmail 1 r "18f3abc..." "Adding context." -cc "manager@example.com" -bcc "audit@example.com"
gmail 1 r "18f3abc..." "Sharing the latest." -atch "/tmp/project_dir"
gmail 1 r -a "18f3abc..." "Please review." -atch "/tmp/notes.txt" "/tmp/project_dir"
gmail 1 r -t "19ca756c06a7ebcd" "Following up on this thread."
gmail 1 r -ta "19ca756c06a7ebcd" "Thanks all."

# Spam flow
gmail 1 si
gmail 1 sc
gmail 1 sa "spam1@example.com,spam2@example.com"
gmail 1 sa -ur
```

Reply flags:
- `-a`: reply-all, keeps original Cc recipients (excluding your own address).
- `-t`: treat target id as `thread_id` instead of `message_id`.
- You can combine as `-ta` or `-at`.
- `-cc`: add comma-separated recipients to Cc for send/reply (trailing option, after required args).
- `-bcc`: add comma-separated recipients to Bcc for send/reply (trailing option, after required args).
- `-atch`: attach one or more file/dir paths; directories are attached as generated `.zip` files (trailing option, after required args).
- `s -v`: open your editor (`$VISUAL`, then `$EDITOR`, else `vim`) with a template (`From/To/Subject/CC/BCC/Body`) and send using filled fields.

Spam flow commands:
- `si` (spam identify): scans unread non-`@gmail.com` messages and counts sender occurrences, then lists senders with more than 5 unread mails and (on confirm) adds them to `spam_senders`.
- `sc` (spam clean): trashes all messages (read + unread) from `spam_senders`.
- `sa "<spam_email1,spam_email2,...>"`: manually add one or more senders to `spam_senders`.
- `sa -ur`: adds senders of all unread messages to `spam_senders` and trashes those unread messages.
- Safety rule: `@gmail.com` sender addresses are never added to `spam_senders`.

Message utilities:
- `mr <message_id>`: mark a single message as read.
- `d <message_id>`: delete a single message.
- `ls -ur [limit]`: list unread messages only; if `limit` is omitted, uses config default list limit.
- `ls -ura [limit]`: interactive unread audit. Without `limit`, audits all unread messages continuously in batches of 10. For each unread message: `s` marks spam (adds sender to `spam_senders` and trashes message), `t` trashes message without spam-list update, `n` leaves message unread, `q` stops audit.
- `ls -ra [limit]`: interactive read-mail audit with the same actions as `-ura`; without `limit`, processes read messages continuously in batches of 10.
- Safety rule: both `-ura` and `-ra` never trash emails from `@gmail.com` senders.

## First run auth

On first command for each preset, browser OAuth opens and saves token for that preset.

## Troubleshooting

- `OAuth access is restricted to test users`: add account under OAuth consent screen test users.
- `access_denied`: Workspace admin policy may be blocking this app/scopes.
- `Invalid JSON in config`: remove trailing commas from config file.
