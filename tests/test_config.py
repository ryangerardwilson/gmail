import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gmail_cli.config import (
    load_config,
    normalize_contacts,
    normalize_sender_list,
    resolve_config_path,
    update_account_contacts,
    update_account_sender_lists,
)
from gmail_cli.errors import ConfigError


class ConfigTests(unittest.TestCase):
    def test_resolve_config_path_prefers_env_override(self) -> None:
        with patch.dict(os.environ, {"GMAIL_CLI_CONFIG": "~/x/custom.json", "XDG_CONFIG_HOME": "/tmp/xdg"}, clear=True):
            self.assertEqual(resolve_config_path(), Path("~/x/custom.json").expanduser())

    def test_resolve_config_path_uses_xdg(self) -> None:
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}, clear=True):
            self.assertEqual(resolve_config_path(), Path("/tmp/xdg/gmail/config.json"))

    def test_resolve_config_path_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_config_path(), Path("~/.config/gmail/config.json").expanduser())

    def test_load_config_validates_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            secret = tmp / "client_secret.json"
            signature = tmp / "sig.txt"
            secret.write_text("{}", encoding="utf-8")
            signature.write_text("Best", encoding="utf-8")

            config_path = tmp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "1": {
                                "email": "user@example.com",
                                "client_secret_file": str(secret),
                                "signature_file": str(signature),
                            }
                        },
                        "defaults": {"list_limit": 5},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)
            self.assertEqual(config.default_list_limit, 5)
            self.assertEqual(config.timezone_offset, "+05:30")
            self.assertEqual(config.accounts["1"].email, "user@example.com")
            self.assertEqual(config.accounts["1"].spam_senders, [])
            self.assertEqual(config.accounts["1"].contacts, {})

    def test_load_config_timezone_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            secret = tmp / "client_secret.json"
            signature = tmp / "sig.txt"
            secret.write_text("{}", encoding="utf-8")
            signature.write_text("Best", encoding="utf-8")
            config_path = tmp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "1": {
                                "email": "user@example.com",
                                "client_secret_file": str(secret),
                                "signature_file": str(signature),
                            }
                        },
                        "defaults": {"list_limit": 5, "timezone_offset": "-07:00"},
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            self.assertEqual(config.timezone_offset, "-07:00")

    def test_load_config_rejects_bad_timezone_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            secret = tmp / "client_secret.json"
            signature = tmp / "sig.txt"
            secret.write_text("{}", encoding="utf-8")
            signature.write_text("Best", encoding="utf-8")
            config_path = tmp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "1": {
                                "email": "user@example.com",
                                "client_secret_file": str(secret),
                                "signature_file": str(signature),
                            }
                        },
                        "defaults": {"timezone_offset": "IST"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_load_config_rejects_bad_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            secret = tmp / "client_secret.json"
            signature = tmp / "sig.txt"
            secret.write_text("{}", encoding="utf-8")
            signature.write_text("Best", encoding="utf-8")
            config_path = tmp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "1": {
                                "email": "user@example.com",
                                "client_secret_file": str(secret),
                                "signature_file": str(signature),
                            }
                        },
                        "defaults": {"list_limit": 0},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_normalize_sender_list(self) -> None:
        normalized = normalize_sender_list([" A@X.COM ", "a@x.com", "b@y.com", 123, ""])
        self.assertEqual(normalized, ["a@x.com", "b@y.com"])

    def test_normalize_contacts(self) -> None:
        contacts = normalize_contacts({" Silvia ": " xyz@hbc.com ", "": "x", "bad": 1})
        self.assertEqual(contacts, {"silvia": "xyz@hbc.com"})

    def test_update_account_sender_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            secret = tmp / "client_secret.json"
            signature = tmp / "sig.txt"
            secret.write_text("{}", encoding="utf-8")
            signature.write_text("Best", encoding="utf-8")
            config_path = tmp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "1": {
                                "email": "user@example.com",
                                "client_secret_file": str(secret),
                                "signature_file": str(signature),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            update_account_sender_lists(
                config_path,
                {
                    "1": ["Spam@X.com", "spam@x.com", "person@gmail.com"],
                },
            )
            config = load_config(config_path)
            self.assertEqual(config.accounts["1"].spam_senders, ["spam@x.com"])

    def test_update_account_contacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            secret = tmp / "client_secret.json"
            signature = tmp / "sig.txt"
            secret.write_text("{}", encoding="utf-8")
            signature.write_text("Best", encoding="utf-8")
            config_path = tmp / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "1": {
                                "email": "user@example.com",
                                "client_secret_file": str(secret),
                                "signature_file": str(signature),
                                "contacts": {"old": "old@example.com"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            update_account_contacts(
                config_path,
                "1",
                {"Silvia": "xyz@hbc.com", "bad": "", "foo": 1},
            )
            config = load_config(config_path)
            self.assertEqual(config.accounts["1"].contacts, {"silvia": "xyz@hbc.com"})


if __name__ == "__main__":
    unittest.main()
