# gmail

Gmail-only CLI for account presets, sending mail, listing/searching mail, replying, contacts, and spam cleanup.

## Install

```sh
./install.sh help
./install.sh version
./install.sh upgrade
```

The installed launcher is written to `~/.local/bin/gmail`. `gmail version` prints the runtime version from `_version.py`; source checkouts keep `0.0.0` until release automation stamps an artifact.

## Commands

```sh
gmail help
gmail version
gmail upgrade

gmail auth ~/Documents/credentials/client_secret.json
gmail config
gmail 1 send in editor
gmail 1 send to person@example.com subject "Hello" body "Body"
gmail 1 list unread from geeta since 2w limit 10
gmail 1 list sent containing proposal since 14d limit 10
gmail 1 list with attachments from geeta limit 10
gmail 1 open message 19caef2cd6494116
gmail 1 reply to 19caef2cd6494116 body "Thanks for the update."
gmail 1 reply to thread 19ca756c06a7ebcd all body "Thanks everyone."
gmail spam clean
gmail timer install
gmail timer disable
gmail timer status
gmail 1 spam inspect
gmail 1 spam clean
gmail 1 spam add unread
gmail 1 spam allow trusted@example.com
gmail 1 contacts list
gmail 1 contacts add boss boss@example.com
gmail 1 contacts delete boss
gmail 1 contacts edit
```

## Config

`gmail config` opens `~/.config/gmail/config.json`, `$XDG_CONFIG_HOME/gmail/config.json`, or `$GMAIL_CLI_CONFIG` when set. Tokens are stored under `~/.local/share/gmail/tokens/` or `$XDG_DATA_HOME/gmail/tokens/`.

```json
{
  "accounts": {
    "1": {
      "email": "you@example.com",
      "client_secret_file": "/home/you/.config/gmail/client_secret.json",
      "signature_file": "/home/you/.config/gmail/signatures/account1.txt",
      "contacts": {
        "boss": "boss@example.com"
      },
      "spam_senders": ["promo@example.com"],
      "spam_excludes": ["trusted@example.com"]
    }
  }
}
```

Send and reply flows append the configured `signature_file` automatically.

## Timer

`gmail timer install` writes one user timer that runs `gmail spam clean` across configured presets. Success notifications go through the Quickshell bar when available, with `notify-send` as fallback.
