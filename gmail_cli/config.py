from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError


@dataclass(frozen=True)
class AccountConfig:
    preset: str
    email: str
    client_secret_file: Path
    signature_file: Path
    spam_senders: list[str] = field(default_factory=list)
    spam_excludes: list[str] = field(default_factory=list)
    contacts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    path: Path
    accounts: dict[str, AccountConfig]
    default_list_limit: int
    timezone_offset: str = "+05:30"


_TIMEZONE_OFFSET_RE = re.compile(r"^[+-](?:[01]\d|2[0-3]):[0-5]\d$")


def validate_timezone_offset(value: Any, config_path: Path) -> str:
    if value is None:
        return "+05:30"
    if not isinstance(value, str):
        raise ConfigError(
            f"Invalid config at {config_path}: defaults.timezone_offset must be a string like '+05:30'"
        )
    normalized = value.strip()
    if not _TIMEZONE_OFFSET_RE.match(normalized):
        raise ConfigError(
            f"Invalid config at {config_path}: defaults.timezone_offset must match ±HH:MM (example: '+05:30')"
        )
    return normalized


def normalize_sender_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        value = item.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def normalize_spam_sender_list(values: Any) -> list[str]:
    out = normalize_sender_list(values)
    return [item for item in out if not item.endswith("@gmail.com")]


def normalize_contacts(values: Any) -> dict[str, str]:
    if values is None:
        return {}
    if not isinstance(values, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        alias = key.strip().lower()
        email = value.strip()
        if not alias or not email:
            continue
        out[alias] = email
    return out


def resolve_config_path() -> Path:
    override = os.getenv("GMAIL_CLI_CONFIG")
    if override:
        return Path(override).expanduser()

    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "gmail" / "config.json"

    return Path("~/.config/gmail/config.json").expanduser()


def token_file_for_preset(preset: str) -> Path:
    return Path("~/.gmail/tokens").expanduser() / f"{preset}.json"


def ensure_token_dirs() -> None:
    gmail_home = Path("~/.gmail").expanduser()
    tokens_dir = gmail_home / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)
    for directory in (gmail_home, tokens_dir):
        try:
            directory.chmod(0o700)
        except OSError:
            # Best effort only; filesystem may not allow mode changes.
            pass


def _validate_account(preset: str, raw: Any, config_path: Path) -> AccountConfig:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Invalid config at {config_path}: accounts['{preset}'] must be an object"
        )

    email = raw.get("email")
    client_secret = raw.get("client_secret_file")
    signature_file = raw.get("signature_file")
    spam_senders = normalize_spam_sender_list(raw.get("spam_senders"))
    spam_excludes = normalize_sender_list(raw.get("spam_excludes"))
    contacts = normalize_contacts(raw.get("contacts"))

    if not isinstance(email, str) or not email.strip():
        raise ConfigError(
            f"Invalid config at {config_path}: accounts['{preset}'].email is required"
        )
    if not isinstance(client_secret, str) or not client_secret.strip():
        raise ConfigError(
            "Invalid config at "
            f"{config_path}: accounts['{preset}'].client_secret_file is required"
        )
    if not isinstance(signature_file, str) or not signature_file.strip():
        raise ConfigError(
            "Invalid config at "
            f"{config_path}: accounts['{preset}'].signature_file is required"
        )

    client_secret_path = Path(client_secret).expanduser()
    if not client_secret_path.exists():
        raise ConfigError(
            "Invalid config at "
            f"{config_path}: client_secret_file not found for preset '{preset}': "
            f"{client_secret_path}"
        )
    signature_path = Path(signature_file).expanduser()
    if not signature_path.exists():
        raise ConfigError(
            "Invalid config at "
            f"{config_path}: signature_file not found for preset '{preset}': "
            f"{signature_path}"
        )

    return AccountConfig(
        preset=preset,
        email=email.strip(),
        client_secret_file=client_secret_path,
        signature_file=signature_path,
        spam_senders=spam_senders,
        spam_excludes=spam_excludes,
        contacts=contacts,
    )


def load_config(path: Path | None = None) -> AppConfig:
    config_path = (path or resolve_config_path()).expanduser()
    if not config_path.exists():
        raise ConfigError(
            f"Config not found: {config_path}. Create it from example_config.json."
        )

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Invalid config at {config_path}: root must be an object")

    accounts_raw = raw.get("accounts")
    if not isinstance(accounts_raw, dict) or not accounts_raw:
        raise ConfigError(f"Invalid config at {config_path}: 'accounts' must be a non-empty object")

    accounts: dict[str, AccountConfig] = {}
    for preset, account_data in accounts_raw.items():
        if not isinstance(preset, str) or not preset.strip():
            raise ConfigError(f"Invalid config at {config_path}: account preset keys must be strings")
        accounts[preset] = _validate_account(preset, account_data, config_path)

    defaults_raw = raw.get("defaults", {})
    if defaults_raw is None:
        defaults_raw = {}
    if not isinstance(defaults_raw, dict):
        raise ConfigError(f"Invalid config at {config_path}: 'defaults' must be an object")

    default_limit = defaults_raw.get("list_limit", 10)
    if not isinstance(default_limit, int) or default_limit <= 0:
        raise ConfigError(
            f"Invalid config at {config_path}: defaults.list_limit must be a positive integer"
        )

    timezone_offset = validate_timezone_offset(defaults_raw.get("timezone_offset"), config_path)

    return AppConfig(
        path=config_path,
        accounts=accounts,
        default_list_limit=default_limit,
        timezone_offset=timezone_offset,
    )


def get_account(config: AppConfig, preset: str) -> AccountConfig:
    account = config.accounts.get(preset)
    if account is None:
        available = ", ".join(sorted(config.accounts.keys()))
        raise ConfigError(
            f"Preset '{preset}' not found in {config.path}. Available presets: {available}"
        )
    return account


def update_account_sender_lists(
    config_path: Path,
    updates: dict[str, list[str]],
) -> None:
    config_path = config_path.expanduser()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Invalid config at {config_path}: root must be an object")
    accounts = raw.get("accounts")
    if not isinstance(accounts, dict):
        raise ConfigError(f"Invalid config at {config_path}: 'accounts' must be an object")

    for preset, spam_list in updates.items():
        account = accounts.get(preset)
        if not isinstance(account, dict):
            continue
        spam_values = normalize_spam_sender_list(spam_list)
        account["spam_senders"] = spam_values

    config_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def update_account_spam_excludes(
    config_path: Path,
    preset: str,
    spam_excludes: list[str],
) -> None:
    config_path = config_path.expanduser()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Invalid config at {config_path}: root must be an object")
    accounts = raw.get("accounts")
    if not isinstance(accounts, dict):
        raise ConfigError(f"Invalid config at {config_path}: 'accounts' must be an object")

    account = accounts.get(preset)
    if not isinstance(account, dict):
        raise ConfigError(
            f"Invalid config at {config_path}: preset '{preset}' not found in accounts"
        )

    account["spam_excludes"] = normalize_sender_list(spam_excludes)
    config_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def update_account_contacts(
    config_path: Path,
    preset: str,
    contacts: dict[str, str],
) -> None:
    config_path = config_path.expanduser()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Invalid config at {config_path}: root must be an object")
    accounts = raw.get("accounts")
    if not isinstance(accounts, dict):
        raise ConfigError(f"Invalid config at {config_path}: 'accounts' must be an object")

    account = accounts.get(preset)
    if not isinstance(account, dict):
        raise ConfigError(
            f"Invalid config at {config_path}: preset '{preset}' not found in accounts"
        )

    account["contacts"] = normalize_contacts(contacts)
    config_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
