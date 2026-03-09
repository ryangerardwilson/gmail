from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AccountConfig, ensure_token_dirs, generate_account_key, token_file_for_account_key
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


@dataclass(frozen=True)
class AuthorizedGmailAccount:
    email: str
    account_key: str
    creds: Credentials


def _load_credentials(token_path: Path) -> Credentials | None:
    if Credentials is None:
        raise ApiError(
            "Missing Google auth dependencies. Install with: pip install -r requirements.txt"
        )
    if token_path.exists():
        return Credentials.from_authorized_user_file(str(token_path), SCOPES)
    return None


def _write_token(token_path: Path, creds: Credentials) -> None:
    token_path.write_text(creds.to_json(), encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass


def authorize_account(client_secret_file: Path) -> AuthorizedGmailAccount:
    if Request is None or InstalledAppFlow is None:
        raise ApiError(
            "Missing Google auth dependencies. Install with: pip install -r requirements.txt"
        )
    ensure_token_dirs()
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_file),
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
    if build is None:
        raise ApiError(
            "Missing Google API dependencies. Install with: pip install -r requirements.txt"
        )
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        email = str(profile.get("emailAddress", "")).strip().lower()
    except Exception as exc:
        raise ApiError(f"Failed to fetch Gmail profile after OAuth: {exc}") from exc
    if not email:
        raise ApiError("Gmail profile lookup returned no email address")
    account_key = generate_account_key(client_secret_file, email)
    _write_token(token_file_for_account_key(account_key), creds)
    return AuthorizedGmailAccount(email=email, account_key=account_key, creds=creds)

def get_credentials(account: AccountConfig) -> Credentials:
    if Request is None or InstalledAppFlow is None:
        raise ApiError(
            "Missing Google auth dependencies. Install with: pip install -r requirements.txt"
        )

    ensure_token_dirs()
    if not account.account_key:
        raise ApiError(
            f"preset {account.preset} is missing account_key; re-run `gmail auth <client_secret_path>`"
        )
    token_path = token_file_for_account_key(account.account_key)
    creds = _load_credentials(token_path)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            raise ApiError(f"Failed to refresh Gmail token for preset {account.preset}: {exc}") from exc
    else:
        creds = authorize_account(account.client_secret_file).creds

    if creds is None:
        raise ApiError("Failed to obtain credentials")

    _write_token(token_path, creds)

    return creds


def build_gmail_service(account: AccountConfig):
    if build is None:
        raise ApiError(
            "Missing Google API dependencies. Install with: pip install -r requirements.txt"
        )
    creds = get_credentials(account)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
