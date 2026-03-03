# Gmail CLI Implementation Plan

## 1. Goal and constraints
- Build a Gmail-only CLI tool that supports:
  - Sending email
  - Searching/listing emails
  - Replying to a specific email by message id
- Tool should be declarative and easy for humans or AI to invoke.
- Support multiple Gmail accounts via presets (starting with 2, scalable to more).
- Use XDG-compliant config path:
  - Default: `~/.config/gmail/config.json`
  - Respect `$XDG_CONFIG_HOME` when set.

## 2. CLI contract (v1)
- Primary entrypoint:
  - `python main.py <preset> s <to> <subject> <body>`
  - `python main.py <preset> ls <query>`
  - `python main.py <preset> r <message_id> <body>`
- Aliases and compatibility:
  - Accept `-s` as alias for `s` if desired.
  - Prefer `ls` over overloaded flags to keep parsing explicit.
- Examples:
  - `python main.py 1 s "xyz@example.com" "this is the subject" "this is the body"`
  - `python main.py 1 ls "from maanas limit 1"`
  - `python main.py 1 r "18f3..." "Thanks, sharing this now."`

## 3. Project structure
- `main.py`: CLI parser and command dispatch.
- `gmail_cli/`
  - `config.py`: XDG config loading + validation.
  - `auth.py`: OAuth/token handling per account preset.
  - `gmail_api.py`: Thin wrapper around Gmail API calls.
  - `query_parser.py`: Declarative search query parsing.
  - `formatters.py`: Terminal output formatting.
  - `errors.py`: Custom exceptions and user-facing error mapping.
- `tests/`
  - unit tests for parser/config logic.
  - optional integration tests (mocked API service).
- `README.md`: setup + command reference.

## 4. Config design (XDG compliant)
- Default config path resolution:
  - If `GMAIL_CLI_CONFIG` is set, use it.
  - Else if `XDG_CONFIG_HOME` set, use `$XDG_CONFIG_HOME/gmail/config.json`.
  - Else use `~/.config/gmail/config.json`.
- Proposed `config.json` shape:
```json
{
  "accounts": {
    "1": {
      "email": "account1@gmail.com",
      "client_secret_file": "/abs/path/client_secret.json"
    },
    "2": {
      "email": "account2@gmail.com",
      "client_secret_file": "/abs/path/client_secret.json"
    }
  },
  "defaults": {
    "list_limit": 10
  }
}
```
- Token storage is not user-configured:
  - Store tokens under `~/.gmail/tokens/`.
  - Use deterministic filenames per preset, e.g. `~/.gmail/tokens/1.json`, `~/.gmail/tokens/2.json`.
  - CLI must auto-create `~/.gmail/tokens/` on demand (no manual setup required).
- Validation rules:
  - Preset keys must be strings (e.g., `"1"`, `"2"`) to match CLI usage.
  - `email`, `client_secret_file` required for each account.
  - Fail fast with actionable error messages.

## 5. Authentication and authorization
- Use Gmail API with OAuth2 installed app flow.
- Scopes (minimal but sufficient):
  - `https://www.googleapis.com/auth/gmail.send`
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/gmail.modify` (needed for reply label/thread workflows)
- Auth flow:
  - On first use of preset, open browser consent flow, store token in `~/.gmail/tokens/<preset>.json`.
  - On subsequent runs, refresh token automatically.
  - Before writing tokens, ensure `~/.gmail/` and `~/.gmail/tokens/` exist; create with restrictive permissions (`0700`) when possible.
- Dependencies:
  - `google-api-python-client`
  - `google-auth-httplib2`
  - `google-auth-oauthlib`

## 6. Command behavior details

### 6.1 Send (`s`)
- Input: `<to> <subject> <body>`
- Build MIME email:
  - `To`, `Subject`, `From` from preset email.
  - Plain text body for v1.
- Gmail API:
  - `users.messages.send(userId="me", body={"raw": base64url(mime)})`
- Output:
  - Success: sent message id + thread id.
  - Failure: clear reason and next action.

### 6.2 List/Search (`ls`)
- Input: `<query_string>` like `"from maanas limit 1"`.
- Declarative parser v1 grammar (simple):
  - tokens by whitespace.
  - recognized keywords: `from`, `to`, `subject`, `limit`, `after`, `before`, `unread`.
  - free words become generic Gmail `q` terms.
- Conversion to Gmail query:
  - `from maanas limit 1` -> Gmail `q="from:maanas"`, `maxResults=1`.
- API sequence:
  - `users.messages.list(userId="me", q=..., maxResults=...)`
  - For each message id, fetch metadata/snippet via `users.messages.get(format="metadata")`.
- Output table fields:
  - local `result_id` (1..N)
  - `message_id`
  - `thread_id`
  - `from`
  - `subject`
  - `date`
  - short `snippet`
- Persist last search mapping (optional v1.1):
  - Save `result_id -> message_id` in cache to allow easier replies.

### 6.3 Reply (`r`)
- Input: `<message_id> <body>`
- API sequence:
  - Fetch original message metadata to determine:
    - `threadId`
    - `Message-ID`, `References`, `Subject`, `From`
  - Build reply MIME with:
    - `To` = original sender (or `Reply-To` if present)
    - `Subject` = `Re: ...` (if missing prefix)
    - `In-Reply-To` and `References` headers
  - Send using `users.messages.send` with `threadId`.
- Output:
  - sent reply id + thread id.

## 7. Error handling and UX
- Standardized error classes for:
  - Missing config
  - Missing preset
  - Invalid query syntax
  - Gmail auth/token errors
  - API failures/rate limits
- User-facing messages should be explicit and actionable:
  - show the preset and config path used.
  - suggest exact remediation steps.
- Exit codes:
  - `0` success
  - `2` usage error
  - `3` config/auth error
  - `4` API error

## 8. Security and privacy
- Never print OAuth tokens.
- Ensure token files are created with restrictive permissions where possible.
- Keep message body output minimal unless explicitly requested.
- Avoid logging full email content in normal mode.

## 9. Testing plan
- Unit tests:
  - config path resolution and schema validation.
  - query parser to Gmail query conversion.
  - CLI argument parsing and dispatch.
  - reply header generation (`Re:`, `In-Reply-To`, `References`).
- Mock API tests:
  - send/list/reply call shapes.
- Manual smoke tests:
  - first-time auth for preset 1 and 2.
  - send between own accounts.
  - search by sender + limit.
  - reply using returned message id.

## 10. Delivery sequence (milestones)
1. Bootstrap project layout + dependencies + README skeleton.
2. Implement config loader and validation.
3. Implement OAuth/account service builder.
4. Implement `s` command.
5. Implement `ls` parser and list output.
6. Implement `r` with proper threading headers.
7. Add tests and polish error handling.
8. Add packaging/entrypoint (`python -m` or console script) if needed.

## 11. Future extensions (post-v1)
- `show <message_id>` to inspect full message.
- Attachments support for send/reply.
- HTML body support.
- account aliases by name (`work`, `personal`) in addition to numeric presets.
- local cache/history for ergonomic references.
