# AGENTS.md

## Workspace Defaults
- Follow `/home/ryan/Generalists/ceo/PRODUCT_PURITY.md` for declarative CLI purity.
- Follow `/home/ryan/Generalists/cto/CANONICAL_REFERENCE_IMPLEMENTATION_FOR_CLI_AND_TUI_APPS.md` for executable contract details such as `help`, `version`, `upgrade`, installer behavior, release workflow expectations, and regression expectations.
- This file only records `gmail`-specific constraints or durable deviations.

## Mission
Implement a Gmail-only CLI in this repository that can:
- send email,
- list/search email,
- reply to email by message id,
using account presets defined in XDG-compliant config.

## Product requirements
- Gmail-only support for v1.
- Multi-account via preset keys (`1`, `2`, ...), scalable to more.
- `auth <client_secret_path>` must be the standard way to add or refresh a Google account preset.
- Global maintenance commands may exist when their semantics are cross-preset.
- Config default path must be XDG-compliant:
  - `$GMAIL_CLI_CONFIG` (if set) overrides all.
  - else `$XDG_CONFIG_HOME/gmail/config.json` (if `XDG_CONFIG_HOME` is set),
  - else `~/.config/gmail/config.json`.
- Token storage must not be user-configured in `config.json`:
  - always use `$XDG_DATA_HOME/gmail/tokens/` or `~/.local/share/gmail/tokens/`,
  - token file naming should use a stable internal account key rather than the preset number.
  - CLI must automatically create the data/token directories if missing.
  - do not keep legacy preset-token fallback logic in the main runtime.
- `signature_file` must remain a per-account config setting, and send/reply flows must append that configured signature automatically.
- CLI interface must support declarative commands such as:
  - `python main.py accounts list`
  - `python main.py setup check`
  - `python main.py <preset> send to <email|alias> subject <subject> body <body>`
  - `python main.py <preset> preview send to <email|alias> subject <subject> body <body>`
  - `python main.py <preset> list [unread|read|sent|starred|external] [from <sender>] [containing <text>] [since <window>] [limit <count>]`
  - `python main.py <preset> list ... output json`
  - `python main.py <preset> inspect message <message_id>`
  - `python main.py <preset> inspect thread <thread_id>`
  - `python main.py <preset> reply to <message_id|thread <thread_id>> [all] body <body>`
  - `python main.py <preset> preview reply to <message_id|thread <thread_id>> [all] body <body>`
- Spam cleanup should support:
  - `python main.py <preset> spam clean`
  - `python main.py spam clean`
- Timer controls should be global:
  - `python main.py timer install`
  - `python main.py timer disable`
  - `python main.py timer status`
- List examples to support:
  - `python main.py 1 list limit 10`
  - `python main.py 1 list with attachments limit 10`
  - `python main.py 1 list from maanas limit 1`
  - `python main.py 1 list containing invoice limit 10`
  - `python main.py 1 list from geeta since 2w limit 10`
  - `python main.py 1 list since "jan 2025" limit 20`

## Architecture expectations
- Keep API boundaries clean:
  - CLI parsing and dispatch in entrypoint module.
  - Config and validation isolated.
  - Gmail API/auth logic isolated.
  - Query parsing isolated.
- Prefer small, testable pure functions for parsing and config handling.
- Avoid hard-coding account credentials or paths.

## Suggested code layout
- `main.py` (entrypoint)
- `gmail_cli/config.py`
- `gmail_cli/auth.py`
- `gmail_cli/gmail_api.py`
- `gmail_cli/query_parser.py`
- `gmail_cli/formatters.py`
- `gmail_cli/errors.py`
- `tests/`

Adjust structure if needed, but preserve separation of concerns.

## Implementation rules
- Use Python 3.11+ style where available.
- Use `argparse` or equivalent explicit parsing (no implicit positional magic).
- Produce deterministic terminal output for machine + human readability.
- Agent-safe commands must be side-effect explicit: `inspect` does not mark
  read or download attachments; `open` may mark read and download attachments;
  `preview` validates sends/replies without sending.
- Return non-zero exit codes for errors with concise actionable messages.
- Never print OAuth tokens or secrets.
- Create token directories/files with restrictive permissions (`0700` dirs where possible).
- Minimize external dependencies beyond Google API/auth packages.

## Gmail API details
- Use OAuth installed-app flow with token persistence per account preset.
- Use least-privilege practical scopes for send/read/reply behavior.
- For send/reply, construct MIME and encode base64url for Gmail API.
- For reply, preserve thread semantics with:
  - `threadId`
  - `In-Reply-To`
  - `References`
  - proper `Re:` subject handling.

## Search/query behavior
- Parse `list` args into:
  - optional `limit <count>`
  - `with attachments` downloadable-attachment filter
  - `from <sender>` sender filter
  - `containing <text>` Gmail full-text term filter
  - `since <window>` time filter
  - Gmail `q` expression plus `maxResults`
- Unknown `list` tokens should fail fast with a short shape error.

## Output contract
- `list` must print rows including:
  - local index/id
  - `message_id`
  - `thread_id`
  - sender/from
  - subject
  - date
  - snippet (short)
- `s` and `r` should print success line(s) including `message_id` and `thread_id`.

## Testing expectations
- Add unit tests for:
  - config path resolution,
  - config validation,
  - query parsing,
  - reply header construction.
- Mock Gmail API in tests; avoid real network calls in CI tests.
- Include one short manual test checklist in `README.md`.

## Documentation expectations
- `README.md` must include:
  - prerequisites (Google Cloud OAuth setup),
  - config file schema with example,
  - command usage examples,
  - common errors and fixes.

## Definition of done
- CLI supports send/list/reply for configured Gmail presets.
- Works with at least two configured accounts.
- Global `spam clean` runs spam cleanup across all configured presets.
- `timer install` installs one hourly user timer that runs the same global spam cleanup command and sends a success notification through the Quickshell bar, with `notify-send` only as a fallback.
- Config path resolution is XDG-compliant.
- `list limit 10`, `list with attachments limit 10`, `list from maanas limit 1`, `list containing invoice limit 10`, `list from geeta since 2w limit 10`, and `list since "jan 2025" limit 20` work.
- Tests for core parsing/config logic pass locally.
- README is sufficient for a new user to run first auth and send an email.
- Do not reintroduce the retired shared CLI contract package, its TOML file, or old compressed commands.
