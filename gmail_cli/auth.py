from __future__ import annotations

from pathlib import Path

from .config import AccountConfig, ensure_token_dirs, token_file_for_preset
from .errors import ApiError

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:  # pragma: no cover - depends on local env
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _load_credentials(token_path: Path) -> Credentials | None:
    if Credentials is None:
        raise ApiError(
            "Missing Google auth dependencies. Install with: pip install -r requirements.txt"
        )
    if token_path.exists():
        return Credentials.from_authorized_user_file(str(token_path), SCOPES)
    return None


def get_credentials(account: AccountConfig) -> Credentials:
    if Request is None or InstalledAppFlow is None:
        raise ApiError(
            "Missing Google auth dependencies. Install with: pip install -r requirements.txt"
        )

    ensure_token_dirs()
    token_path = token_file_for_preset(account.preset)

    creds = _load_credentials(token_path)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            raise ApiError(f"Failed to refresh Gmail token for preset {account.preset}: {exc}") from exc
    else:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(account.client_secret_file),
                SCOPES,
            )
            creds = flow.run_local_server(
                port=0,
                authorization_prompt_message="Authorize in the browser window. Return here after approval.",
                success_message="Authorization complete. You can close this tab.",
            )
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            raise ApiError(
                "OAuth authorization failed. Ensure the account is an allowed test user "
                f"and the Gmail API is enabled. Details: {exc}"
            ) from exc

    if creds is None:
        raise ApiError("Failed to obtain credentials")

    token_path.write_text(creds.to_json(), encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass

    return creds


def build_gmail_service(account: AccountConfig):
    if build is None:
        raise ApiError(
            "Missing Google API dependencies. Install with: pip install -r requirements.txt"
        )
    creds = get_credentials(account)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
