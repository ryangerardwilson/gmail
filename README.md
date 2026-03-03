# Gmail CLI

Declarative Gmail-only CLI with multi-account presets.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

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
      "signature_file": "/home/you/.config/gmail/signatures/account1.txt"
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
python main.py -v
python main.py -u
python main.py <preset> s <to> <subject> <body>
python main.py <preset> ls <query>
python main.py <preset> ls -t <thread_id>
python main.py <preset> r <message_id> <body>
```

Examples:

```bash
python main.py 1 s "xyz@example.com" "this is the subject" "this is the body"
python main.py 1 ls "from maanas limit 1"
python main.py 1 ls "to silvia limit 1"
python main.py 1 ls -t "19ca756c06a7ebcd"
python main.py 1 r "18f3abc..." "Thanks, sharing this now."
```

## First run auth

On first command for each preset, browser OAuth opens and saves token for that preset.

## Troubleshooting

- `OAuth access is restricted to test users`: add account under OAuth consent screen test users.
- `access_denied`: Workspace admin policy may be blocking this app/scopes.
- `Invalid JSON in config`: remove trailing commas from config file.
