# AGENTS.md

## Mission
Implement a Gmail-only declarative CLI in this repository that can:
- send email,
- list/search email,
- reply to email by message id,
using account presets defined in XDG-compliant config.

## Product requirements
- Gmail-only support for v1.
- Multi-account via preset keys (`1`, `2`, ...), scalable to more.
- `auth <client_secret_path>` must be the standard way to add or refresh a Google account preset.
- Config default path must be XDG-compliant:
  - `$GMAIL_CLI_CONFIG` (if set) overrides all.
  - else `$XDG_CONFIG_HOME/gmail/config.json` (if `XDG_CONFIG_HOME` is set),
  - else `~/.config/gmail/config.json`.
- Token storage must not be user-configured in `config.json`:
  - always use `$XDG_DATA_HOME/gmail/tokens/` or `~/.local/share/gmail/tokens/`,
  - token file naming should use a stable internal account key rather than the preset number.
  - CLI must automatically create the data/token directories if missing.
  - do not keep legacy preset-token fallback logic in the main runtime.
- Declarative CLI interface must support:
  - `python main.py <preset> s <to> <subject> <body>`
  - `python main.py <preset> ls <query>`
  - `python main.py <preset> r <message_id> <body>`
- Query example to support: `"from maanas limit 1"`.

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
- Parse declarative query string into:
  - Gmail `q` expression
  - optional `maxResults` (from `limit`).
- v1 keywords to support:
  - `from`, `to`, `subject`, `limit`, `after`, `before`, `unread`.
- Unknown tokens should fall back to generic Gmail search terms rather than hard fail.

## Output contract
- `ls` must print rows including:
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
- Config path resolution is XDG-compliant.
- Query example `from maanas limit 1` works.
- Tests for core parsing/config logic pass locally.
- README is sufficient for a new user to run first auth and send an email.
