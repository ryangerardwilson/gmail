#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gmail_cli.config import get_account, load_config, token_file_for_account_key, token_file_for_preset
from gmail_cli.errors import ConfigError


def migrate_preset_token(preset: str) -> Path:
    account = get_account(load_config(), preset)
    if not account.account_key:
        raise ConfigError(f"preset '{preset}' is missing account_key; re-run `gmail auth <client_secret_path>`")
    target_path = token_file_for_account_key(account.account_key)
    if target_path.exists():
        return target_path
    preset_token_path = token_file_for_preset(account.preset)
    if not preset_token_path.exists():
        raise ConfigError(f"no legacy token found for preset '{preset}'")
    preset_token_path.rename(target_path)
    return target_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/migrate_legacy_tokens.py",
        add_help=True,
        description="One-time migration of legacy gmail token filenames to account-key names.",
    )
    parser.add_argument("presets", nargs="*", help="Preset ids to migrate. Default: all presets.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    config = load_config()
    presets = args.presets or sorted(config.accounts)
    if not presets:
        print("no presets configured", file=sys.stderr)
        return 1
    for preset in presets:
        try:
            target = migrate_preset_token(preset)
        except ConfigError as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            return exc.exit_code
        print(f"migrated\t{preset}\t{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
