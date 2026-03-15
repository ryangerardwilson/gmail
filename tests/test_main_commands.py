import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from gmail_cli.errors import ApiError, UsageError
from main import (
    _build_runtime_command,
    _handle_delete,
    _handle_list,
    _handle_mark_spammer,
    _handle_mark_read,
    _handle_mark_read_all,
    _handle_mark_star,
    _handle_mark_unstar,
    _handle_mark_unread,
    _handle_open_message,
    _parse_editor_template,
    _handle_reply,
    _handle_send,
    main,
)
from gmail_cli.config import AccountConfig, AppConfig


class MainCommandTests(unittest.TestCase):
    def test_main_global_spam_clean_runs_all_presets(self) -> None:
        account1 = AccountConfig(
            preset="1",
            email="one@example.com",
            client_secret_file=Path("/tmp/client1.json"),
            signature_file=Path("/tmp/signature1.txt"),
        )
        account2 = AccountConfig(
            preset="2",
            email="two@example.com",
            client_secret_file=Path("/tmp/client2.json"),
            signature_file=Path("/tmp/signature2.txt"),
        )
        config = AppConfig(
            path=Path("/tmp/config.json"),
            accounts={"1": account1, "2": account2},
            default_list_limit=5,
        )
        result1 = MagicMock()
        result1.trashed_spam = 3
        result2 = MagicMock()
        result2.trashed_spam = 4
        with patch("main.load_config", return_value=config), patch(
            "main.build_gmail_service", side_effect=[MagicMock(), MagicMock()]
        ) as service_mock, patch(
            "main.run_cleanup_for_account", side_effect=[result1, result2]
        ) as cleanup_mock:
            code = main(["sc"])
        self.assertEqual(code, 0)
        self.assertEqual(service_mock.call_count, 2)
        self.assertEqual(cleanup_mock.call_count, 2)

    def test_main_global_timer_install(self) -> None:
        with patch("main._write_timer_units") as write_mock, patch(
            "main._systemctl_user"
        ) as systemctl_mock:
            code = main(["ti"])
        self.assertEqual(code, 0)
        write_mock.assert_called_once()
        systemctl_mock.assert_any_call("daemon-reload")
        systemctl_mock.assert_any_call("enable", "--now", "gmail.timer")

    def test_help_is_human_friendly(self) -> None:
        with patch("sys.stdout", new=StringIO()) as stdout:
            code = main(["-h"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("Gmail CLI", output)
        self.assertIn("features:", output)
        self.assertIn("# gmail auth <client_secret_path>", output)
        self.assertIn("# gmail <preset> s|ls|o|r ...", output)
        self.assertIn("# gmail sc | gmail ti | gmail td | gmail st | gmail <preset> si|sc|sa|se", output)
        self.assertIn("gmail 1 ls -l 10", output)
        self.assertIn("gmail 1 cn -e", output)
        self.assertNotIn("commands:", output)
        self.assertNotIn("usage:", output)

    def test_build_runtime_command_uses_launcher_only_when_frozen(self) -> None:
        with patch("sys.executable", "/tmp/gmail"), patch("sys.frozen", True, create=True):
            self.assertEqual(_build_runtime_command("sc"), "/tmp/gmail sc")

    def test_write_timer_units_uses_launcher_only_when_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("main.Path.home", return_value=home), patch(
                "sys.executable", "/tmp/gmail"
            ), patch("sys.frozen", True, create=True):
                from main import _write_timer_units

                _write_timer_units()
            service_path = home / ".config" / "systemd" / "user" / "gmail.service"
            service_body = service_path.read_text(encoding="utf-8")
            self.assertIn("ExecStart=/usr/bin/env bash -lc '/tmp/gmail sc &&", service_body)
            self.assertNotIn("main.py sc", service_body)

    def test_main_global_sc_rejects_extra_args(self) -> None:
        with self.assertRaises(UsageError):
            main(["sc", "extra"])

    def test_parse_editor_template(self) -> None:
        content = "\n".join(
            [
                "From: me@example.com",
                "To: user@example.com",
                "Subject: Hello",
                "CC: cc1@example.com, cc2@example.com",
                "BCC: audit@example.com",
                "Body:",
                "",
                "Line1",
                "Line2",
            ]
        )
        parsed = _parse_editor_template(content)
        self.assertEqual(parsed[0], "user@example.com")
        self.assertEqual(parsed[1], "Hello")
        self.assertEqual(parsed[2], "Line1\nLine2")
        self.assertEqual(parsed[3], ["cc1@example.com", "cc2@example.com"])
        self.assertEqual(parsed[4], ["audit@example.com"])
        self.assertEqual(parsed[5], [])

    def test_parse_editor_template_missing_fields_is_allowed(self) -> None:
        to_email, subject, body, cc_emails, bcc_emails, attachment_paths = _parse_editor_template(
            "Subject: x\nBody:\nhello"
        )
        self.assertEqual(to_email, "")
        self.assertEqual(subject, "x")
        self.assertEqual(body, "hello")
        self.assertEqual(cc_emails, [])
        self.assertEqual(bcc_emails, [])
        self.assertEqual(attachment_paths, [])

    def test_parse_editor_template_attachment_csv(self) -> None:
        with patch("main._parse_attachment_path", side_effect=[Path("/tmp/a"), Path("/tmp/b")]):
            _, _, _, _, _, attachment_paths = _parse_editor_template(
                'To: x@y.com\nSubject: hi\nAttachments: "/tmp/a, /tmp/b"\nBody:\nbody'
            )
        self.assertEqual(attachment_paths, [Path("/tmp/a"), Path("/tmp/b")])

    def test_handle_send_editor_mode(self) -> None:
        service = MagicMock()
        with patch(
            "main._open_editor_template",
            return_value=(
                "to@example.com",
                "Subject",
                "Body",
                ["cc@example.com"],
                ["bcc@example.com"],
                [Path("/tmp/a")],
            ),
        ) as editor_mock, patch("main.send_email", return_value={"id": "m1", "threadId": "t1"}) as send_mock:
            code = _handle_send(service, "me@example.com", ["-e"], "sig", {})

        self.assertEqual(code, 0)
        editor_mock.assert_called_once_with("me@example.com", "sig", include_to_subject=True)
        send_mock.assert_called_once()
        args, kwargs = send_mock.call_args
        self.assertEqual(args[2], "to@example.com")
        self.assertEqual(kwargs["cc_emails"], ["cc@example.com"])
        self.assertEqual(kwargs["bcc_emails"], ["bcc@example.com"])
        self.assertEqual(kwargs["attachment_paths"], [Path("/tmp/a")])

    def test_handle_send_editor_mode_missing_required_fields_cancels(self) -> None:
        service = MagicMock()
        with patch(
            "main._open_editor_template",
            return_value=("", "Subject", "Body", [], [], []),
        ), patch("main.send_email") as send_mock:
            code = _handle_send(service, "me@example.com", ["-e"], "sig", {})
        self.assertEqual(code, 0)
        send_mock.assert_not_called()

    def test_handle_send_editor_mode_failure_prints_draft(self) -> None:
        service = MagicMock()
        with patch(
            "main._open_editor_template",
            return_value=("to@example.com", "Subject", "Body", [], [], []),
        ), patch("main.send_email", side_effect=ApiError("boom")), patch("main.print") as print_mock:
            with self.assertRaises(ApiError):
                _handle_send(service, "me@example.com", ["-e"], "sig", {})
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("editor_draft_recovery:", printed)
        self.assertIn("Body", printed)

    def test_handle_reply_editor_mode(self) -> None:
        service = MagicMock()
        with patch(
            "main._open_editor_template",
            return_value=(
                "",
                "",
                "Reply body",
                ["cc-from-editor@example.com"],
                ["bcc-from-editor@example.com"],
                [Path("/tmp/attach-from-editor")],
            ),
        ) as editor_mock, patch(
            "main._parse_attachment_path",
            return_value=Path("/tmp/attach-cli"),
        ), patch(
            "main.reply_to_message",
            return_value={"id": "m1", "threadId": "t1"},
        ) as reply_mock:
            code = _handle_reply(
                service,
                "me@example.com",
                ["-e", "msg1", "-cc", "cc-cli@example.com", "-atch", "/tmp/attach-cli"],
                "sig",
                {},
            )
        self.assertEqual(code, 0)
        editor_mock.assert_called_once_with("me@example.com", "sig", include_to_subject=False)
        reply_mock.assert_called_once()
        args, kwargs = reply_mock.call_args
        self.assertEqual(args[2], "msg1")
        self.assertEqual(kwargs["cc_emails"], ["cc-from-editor@example.com", "cc-cli@example.com"])
        self.assertEqual(kwargs["bcc_emails"], ["bcc-from-editor@example.com"])
        self.assertEqual(
            kwargs["attachment_paths"],
            [Path("/tmp/attach-from-editor"), Path("/tmp/attach-cli")],
        )

    def test_handle_reply_editor_mode_empty_body_cancels(self) -> None:
        service = MagicMock()
        with patch(
            "main._open_editor_template",
            return_value=("", "", "", [], [], []),
        ), patch("main.reply_to_message") as reply_mock:
            code = _handle_reply(service, "me@example.com", ["-e", "msg1"], "sig", {})
        self.assertEqual(code, 0)
        reply_mock.assert_not_called()

    def test_handle_reply_editor_mode_failure_prints_draft_and_hint(self) -> None:
        service = MagicMock()
        with patch(
            "main._open_editor_template",
            return_value=("", "", "Reply body", [], [], []),
        ), patch("main.reply_to_message", side_effect=ApiError("not found")), patch(
            "main.print"
        ) as print_mock:
            with self.assertRaises(ApiError):
                _handle_reply(service, "me@example.com", ["-e", "thread_like_id"], "sig", {})
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("editor_draft_recovery:", printed)
        self.assertIn("hint: if this id is a thread id, use: r -e -t <thread_id>", printed)
        self.assertIn("Reply body", printed)

    def test_handle_reply_appends_signature(self) -> None:
        service = MagicMock()
        with patch(
            "main.reply_to_message",
            return_value={"id": "m1", "threadId": "t1"},
        ) as reply_mock:
            code = _handle_reply(
                service,
                "me@example.com",
                ["msg1", "Reply body"],
                "sig",
                {},
            )
        self.assertEqual(code, 0)
        args, kwargs = reply_mock.call_args
        self.assertEqual(args[3], "Reply body")
        self.assertEqual(kwargs["signature"], "sig")

    def test_handle_reply_editor_mode_avoids_duplicate_signature(self) -> None:
        service = MagicMock()
        with patch(
            "main._open_editor_template",
            return_value=("", "", "Reply body\n\n-- \nsig", [], [], []),
        ), patch(
            "main.reply_to_message",
            return_value={"id": "m1", "threadId": "t1"},
        ) as reply_mock:
            code = _handle_reply(service, "me@example.com", ["-e", "msg1"], "sig", {})
        self.assertEqual(code, 0)
        args, kwargs = reply_mock.call_args
        self.assertEqual(args[3], "Reply body\n\n-- \nsig")
        self.assertEqual(kwargs["signature"], "sig")

    def test_handle_send_resolves_contact_alias(self) -> None:
        service = MagicMock()
        with patch("main.send_email", return_value={"id": "m1", "threadId": "t1"}) as send_mock:
            code = _handle_send(
                service,
                "me@example.com",
                ["silvia", "Subject", "Body", "-cc", "team,person@example.com"],
                "sig",
                {"silvia": "xyz@hbc.com", "team": "team@example.com"},
            )
        self.assertEqual(code, 0)
        args, kwargs = send_mock.call_args
        self.assertEqual(args[2], "xyz@hbc.com")
        self.assertEqual(kwargs["cc_emails"], ["team@example.com", "person@example.com"])

    def test_cn_add_contact(self) -> None:
        with patch("main.load_config") as load_config_mock, patch(
            "main.get_account"
        ) as get_account_mock, patch("main.build_gmail_service"), patch(
            "main._read_signature", return_value="sig"
        ), patch("main.update_account_contacts") as update_mock:
            config = MagicMock()
            config.path = Path("/tmp/config.json")
            load_config_mock.return_value = config
            get_account_mock.return_value = AccountConfig(
                preset="1",
                email="me@example.com",
                client_secret_file=MagicMock(),
                signature_file=MagicMock(),
                contacts={"old": "old@example.com"},
            )
            code = main(["1", "cn", "-a", "silvia", "xyz@hbc.com"])
        self.assertEqual(code, 0)
        update_mock.assert_called_once_with(
            Path("/tmp/config.json"),
            "1",
            {"old": "old@example.com", "silvia": "xyz@hbc.com"},
        )

    def test_cn_delete_contact(self) -> None:
        with patch("main.load_config") as load_config_mock, patch(
            "main.get_account"
        ) as get_account_mock, patch("main.build_gmail_service"), patch(
            "main._read_signature", return_value="sig"
        ), patch("main.update_account_contacts") as update_mock:
            config = MagicMock()
            config.path = Path("/tmp/config.json")
            load_config_mock.return_value = config
            get_account_mock.return_value = AccountConfig(
                preset="1",
                email="me@example.com",
                client_secret_file=MagicMock(),
                signature_file=MagicMock(),
                contacts={"silvia": "xyz@hbc.com"},
            )
            code = main(["1", "cn", "-d", "silvia"])
        self.assertEqual(code, 0)
        update_mock.assert_called_once_with(Path("/tmp/config.json"), "1", {})

    def test_cn_list_no_contacts(self) -> None:
        with patch("main.load_config") as load_config_mock, patch(
            "main.get_account"
        ) as get_account_mock, patch("main.build_gmail_service"), patch(
            "main._read_signature", return_value="sig"
        ):
            config = MagicMock()
            config.path = Path("/tmp/config.json")
            load_config_mock.return_value = config
            get_account_mock.return_value = AccountConfig(
                preset="1",
                email="me@example.com",
                client_secret_file=MagicMock(),
                signature_file=MagicMock(),
                contacts={},
            )
            code = main(["1", "cn"])
        self.assertEqual(code, 0)

    def test_handle_list_unread_default_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_all_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            code = _handle_list(service, ["-ur"], default_limit=10, my_email="me@example.com")
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "is:unread -in:sent")

    def test_handle_list_unread_custom_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-ur", "-l", "1"], default_limit=10, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "is:unread -in:sent", 1)

    def test_handle_list_unread_bad_limit(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_list(service, ["-ur", "0"], default_limit=10, my_email="me@example.com")

    def test_handle_list_read_default_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_all_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            code = _handle_list(service, ["-r"], default_limit=10, my_email="me@example.com")
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "is:read -in:sent")

    def test_handle_list_read_custom_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-r", "-l", "1"], default_limit=10, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "is:read -in:sent", 1)

    def test_handle_list_starred_default_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_all_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            code = _handle_list(service, ["-str"], default_limit=10, my_email="me@example.com")
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "is:starred -in:sent")

    def test_handle_list_starred_custom_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-str", "-l", "3"], default_limit=10, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "is:starred -in:sent", 3)

    def test_handle_list_external_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            code = _handle_list(service, ["-ext", "-l", "10"], default_limit=10, my_email="me@example.com")
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(
            service, "-from:me@example.com -in:sent -from:*@example.com", 10
        )

    def test_handle_list_sent_default_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_all_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            code = _handle_list(service, ["-snt"], default_limit=10, my_email="me@example.com")
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "in:sent")

    def test_handle_list_sent_custom_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-snt", "-l", "10"], default_limit=5, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "in:sent", 10)

    def test_handle_list_sent_contains_filter(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[]) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-snt", "-c", "silvia"], default_limit=5, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "in:sent silvia", 5)

    def test_handle_list_sent_time_limit_filter(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[]) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-snt", "-tl", "jan 2025"], default_limit=5, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(
            service,
            "in:sent after:2024/12/31 before:2025/02/01",
            5,
        )

    def test_handle_list_attachment_only_default_limit(self) -> None:
        service = MagicMock()
        messages = [
            {"id": "ics-only", "payload": {"parts": [{"filename": "invite.ics", "body": {"attachmentId": "a1"}}]}},
            {"id": "pdf", "payload": {"parts": [{"filename": "notes.pdf", "body": {"attachmentId": "a2"}}]}},
        ]
        with patch("main.list_messages_page", return_value=(messages, None)) as page_mock, patch(
            "main.render_messages_table", return_value="table"
        ) as render_mock:
            code = _handle_list(service, ["-wa"], default_limit=5, my_email="me@example.com")
        self.assertEqual(code, 0)
        page_mock.assert_called_once_with(service, "-in:sent has:attachment", 5, page_token=None)
        render_mock.assert_called_once_with([messages[1]], "me@example.com", utc_offset="+05:30")

    def test_handle_list_attachment_only_paginates_past_ics_only_messages(self) -> None:
        service = MagicMock()
        ics_only = [{"id": "ics-only", "payload": {"parts": [{"filename": "invite.ics", "body": {"attachmentId": "a1"}}]}}]
        pdf = [{"id": "pdf", "payload": {"parts": [{"filename": "notes.pdf", "body": {"attachmentId": "a2"}}]}}]
        with patch(
            "main.list_messages_page",
            side_effect=[(ics_only, "token-1"), (pdf, None)],
        ) as page_mock, patch("main.render_messages_table", return_value="table") as render_mock:
            code = _handle_list(service, ["-wa", "-l", "1"], default_limit=5, my_email="me@example.com")
        self.assertEqual(code, 0)
        self.assertEqual(page_mock.call_count, 2)
        render_mock.assert_called_once_with(pdf, "me@example.com", utc_offset="+05:30")

    def test_handle_list_attachment_only_rejects_audit_mode(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_list(service, ["-wa", "-ura", "-l", "1"], default_limit=10, my_email="me@example.com")

    def test_handle_list_limit_only(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[]) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-l", "10"], default_limit=5, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "-in:sent", 10)

    def test_handle_list_time_limit_filter(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[]) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-f", "geeta", "-tl", "2w"], default_limit=5, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "-in:sent from:geeta newer_than:14d", 5)

    def test_handle_list_open_mode_prints_bodies_and_marks_read(self) -> None:
        service = MagicMock()
        messages = [{"id": "m1"}, {"id": "m2"}]
        with patch("main.list_messages", return_value=messages) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ) as render_table_mock, patch("main.render_message_open", return_value="opened") as render_open_mock, patch(
            "main.batch_mark_messages_read", return_value=2
        ) as mark_batch_mock:
            code = _handle_list(
                service,
                ["-o", "-ur", "-l", "2"],
                default_limit=10,
                my_email="me@example.com",
            )
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "is:unread -in:sent", 2)
        render_table_mock.assert_not_called()
        self.assertEqual(render_open_mock.call_count, 2)
        mark_batch_mock.assert_called_once_with(service, ["m1", "m2"])

    def test_handle_list_open_mode_with_from_filter(self) -> None:
        service = MagicMock()
        messages = [{"id": "m1"}]
        with patch("main.list_messages", return_value=messages) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ) as render_table_mock, patch("main.render_message_open", return_value="opened"), patch(
            "main.batch_mark_messages_read", return_value=1
        ) as mark_batch_mock:
            code = _handle_list(
                service,
                ["-f", "xyz", "-l", "1", "-o"],
                default_limit=10,
                my_email="me@example.com",
            )
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "-in:sent from:xyz", 1)
        render_table_mock.assert_not_called()
        mark_batch_mock.assert_called_once_with(service, ["m1"])

    def test_handle_list_open_mode_rejects_audit_flags(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_list(service, ["-o", "-ura", "-l", "1"], default_limit=10, my_email="me@example.com")

    def test_handle_mark_read(self) -> None:
        service = MagicMock()
        with patch("main.mark_message_read", return_value={"id": "m1", "threadId": "t1"}) as mark_mock:
            code = _handle_mark_read(service, ["m1"])
        self.assertEqual(code, 0)
        mark_mock.assert_called_once_with(service, "m1")

    def test_handle_delete(self) -> None:
        service = MagicMock()
        with patch("main.delete_message") as delete_mock:
            code = _handle_delete(service, ["m1"])
        self.assertEqual(code, 0)
        delete_mock.assert_called_once_with(service, "m1")

    def test_handle_open_message(self) -> None:
        service = MagicMock()
        with patch("main.get_message", return_value={"id": "m1", "threadId": "t1"}), patch(
            "main.hydrate_message_text_bodies",
            side_effect=lambda _service, message: message,
        ) as hydrate_mock, patch(
            "main.hydrate_message_text_from_raw",
            side_effect=lambda _service, message: message,
        ) as hydrate_raw_mock, patch(
            "main.download_message_attachments", return_value=[]
        ) as dl_mock, patch(
            "main.mark_message_read", return_value={"id": "m1", "threadId": "t1"}
        ) as mark_mock, patch("main.render_message_open", return_value="opened"):
            code = _handle_open_message(service, ["m1"], "me@example.com", "1")
        self.assertEqual(code, 0)
        hydrate_mock.assert_called_once()
        hydrate_raw_mock.assert_called_once()
        dl_mock.assert_called_once_with(service, {"id": "m1", "threadId": "t1"}, Path.cwd() / "atch_1_m1")
        mark_mock.assert_called_once_with(service, "m1")

    def test_handle_open_thread(self) -> None:
        service = MagicMock()
        messages = [{"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t1"}]
        with patch("main.get_thread_messages", return_value=messages), patch(
            "main.hydrate_message_text_bodies",
            side_effect=lambda _service, message: message,
        ) as hydrate_mock, patch(
            "main.hydrate_message_text_from_raw",
            side_effect=lambda _service, message: message,
        ) as hydrate_raw_mock, patch(
            "main.download_message_attachments", side_effect=[[Path("/tmp/a")], []]
        ) as dl_mock, patch(
            "main.batch_mark_messages_read", return_value=2
        ) as mark_batch_mock, patch(
            "main.render_message_open", return_value="opened"
        ):
            code = _handle_open_message(service, ["-t", "t1"], "me@example.com", "1")
        self.assertEqual(code, 0)
        self.assertEqual(hydrate_mock.call_count, 2)
        self.assertEqual(hydrate_raw_mock.call_count, 2)
        self.assertEqual(
            dl_mock.call_args_list,
            [
                unittest.mock.call(service, {"id": "m1", "threadId": "t1"}, Path.cwd() / "atch_1_m1"),
                unittest.mock.call(service, {"id": "m2", "threadId": "t1"}, Path.cwd() / "atch_1_m2"),
            ],
        )
        mark_batch_mock.assert_called_once_with(service, ["m1", "m2"])

    def test_handle_mark_unread(self) -> None:
        service = MagicMock()
        with patch("main.mark_message_unread", return_value={"id": "m1", "threadId": "t1"}) as mark_mock:
            code = _handle_mark_unread(service, ["m1"])
        self.assertEqual(code, 0)
        mark_mock.assert_called_once_with(service, "m1")

    def test_handle_mark_star(self) -> None:
        service = MagicMock()
        with patch("main.star_message", return_value={"id": "m1", "threadId": "t1"}) as star_mock:
            code = _handle_mark_star(service, ["m1"])
        self.assertEqual(code, 0)
        star_mock.assert_called_once_with(service, "m1")

    def test_handle_mark_unstar(self) -> None:
        service = MagicMock()
        with patch("main.unstar_message", return_value={"id": "m1", "threadId": "t1"}) as unstar_mock:
            code = _handle_mark_unstar(service, ["m1"])
        self.assertEqual(code, 0)
        unstar_mock.assert_called_once_with(service, "m1")

    def test_handle_mark_star_bad_args(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_mark_star(service, [])

    def test_handle_mark_unstar_bad_args(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_mark_unstar(service, [])

    def test_handle_mark_read_all(self) -> None:
        service = MagicMock()
        with patch("main.list_message_ids", return_value=["m1", "m2"]) as list_ids_mock, patch(
            "main.batch_mark_messages_read", return_value=2
        ) as batch_mock:
            code = _handle_mark_read_all(service, [])
        self.assertEqual(code, 0)
        list_ids_mock.assert_called_once_with(service, "is:unread")
        batch_mock.assert_called_once_with(service, ["m1", "m2"])

    def test_handle_mark_spammer(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
        )
        config = MagicMock()
        config.path = Path("/tmp/config.json")
        with patch(
            "main.get_message",
            return_value={"payload": {"headers": [{"name": "From", "value": "Spam <spam@x.com>"}]}},
        ), patch("main.update_account_sender_lists") as update_mock, patch(
            "main.delete_message"
        ) as delete_mock:
            code = _handle_mark_spammer(config, account, service, ["m1"])
        self.assertEqual(code, 0)
        update_mock.assert_called_once_with(Path("/tmp/config.json"), {"1": ["spam@x.com"]})
        delete_mock.assert_called_once_with(service, "m1")

    def test_upgrade_rejects_extra_args(self) -> None:
        with self.assertRaises(SystemExit):
            main(["-u", "3"])

    def test_sa_adds_spam_senders(self) -> None:
        with patch("main.load_config") as load_config_mock, patch(
            "main.get_account"
        ) as get_account_mock, patch("main.build_gmail_service"), patch(
            "main._read_signature", return_value="sig"
        ), patch("main.update_account_sender_lists") as update_mock:
            config = MagicMock()
            config.path = Path("/tmp/config.json")
            load_config_mock.return_value = config
            get_account_mock.return_value = AccountConfig(
                preset="1",
                email="me@example.com",
                client_secret_file=MagicMock(),
                signature_file=MagicMock(),
                spam_senders=["old@spam.com"],
            )
            code = main(["1", "sa", "new@spam.com,old@spam.com"])
        self.assertEqual(code, 0)
        update_mock.assert_called_once()

    def test_se_adds_spam_excludes(self) -> None:
        with patch("main.load_config") as load_config_mock, patch(
            "main.get_account"
        ) as get_account_mock, patch("main.build_gmail_service"), patch(
            "main._read_signature", return_value="sig"
        ), patch("main.update_account_spam_excludes") as update_mock:
            config = MagicMock()
            config.path = Path("/tmp/config.json")
            load_config_mock.return_value = config
            get_account_mock.return_value = AccountConfig(
                preset="1",
                email="me@example.com",
                client_secret_file=MagicMock(),
                signature_file=MagicMock(),
                spam_excludes=["old@example.com"],
            )
            code = main(["1", "se", "trusted@example.com,old@example.com"])
        self.assertEqual(code, 0)
        update_mock.assert_called_once_with(
            Path("/tmp/config.json"),
            "1",
            ["old@example.com", "trusted@example.com"],
        )

    def test_se_adds_spam_exclude_domains(self) -> None:
        with patch("main.load_config") as load_config_mock, patch(
            "main.get_account"
        ) as get_account_mock, patch("main.build_gmail_service"), patch(
            "main._read_signature", return_value="sig"
        ), patch("main.update_account_spam_excludes") as update_mock:
            config = MagicMock()
            config.path = Path("/tmp/config.json")
            load_config_mock.return_value = config
            get_account_mock.return_value = AccountConfig(
                preset="1",
                email="me@example.com",
                client_secret_file=MagicMock(),
                signature_file=MagicMock(),
                spam_excludes=[],
            )
            code = main(["1", "se", "@blocked.com,@another.com"])
        self.assertEqual(code, 0)
        update_mock.assert_called_once_with(
            Path("/tmp/config.json"),
            "1",
            ["@another.com", "@blocked.com"],
        )

    def test_sa_unread_mode_collects_and_trashes(self) -> None:
        with patch("main.load_config") as load_config_mock, patch(
            "main.get_account"
        ) as get_account_mock, patch("main.build_gmail_service") as build_service_mock, patch(
            "main._read_signature", return_value="sig"
        ), patch("main.list_messages_page") as list_page_mock, patch(
            "main.batch_delete_messages", return_value=2
        ) as trash_mock, patch("main.update_account_sender_lists") as update_mock:
            config = MagicMock()
            config.path = Path("/tmp/config.json")
            load_config_mock.return_value = config
            get_account_mock.return_value = AccountConfig(
                preset="1",
                email="me@example.com",
                client_secret_file=MagicMock(),
                signature_file=MagicMock(),
                spam_senders=[],
            )
            service = MagicMock()
            build_service_mock.return_value = service
            list_page_mock.side_effect = (
                [
                    (
                        [
                            {
                                "id": "m1",
                                "threadId": "t1",
                                "snippet": "hello",
                                "payload": {
                                    "headers": [
                                        {"name": "From", "value": "A <a@x.com>"},
                                        {"name": "Subject", "value": "S"},
                                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                                    ]
                                },
                            },
                            {
                                "id": "m2",
                                "threadId": "t2",
                                "snippet": "hello",
                                "payload": {
                                    "headers": [
                                        {"name": "From", "value": "B <b@gmail.com>"},
                                        {"name": "Subject", "value": "S"},
                                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                                    ]
                                },
                            },
                        ],
                        None,
                    )
                ]
            )
            code = main(["1", "sa", "-ur"])

        self.assertEqual(code, 0)
        update_payload = update_mock.call_args.args[1]
        self.assertEqual(sorted(update_payload["1"]), ["a@x.com"])
        trash_mock.assert_called_once_with(service, ["m1", "m2"])

    def test_handle_list_unread_audit_bad_limit(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_list(
                service,
                ["-ura", "-l", "0"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/x",
                account=MagicMock(),
            )

    def test_handle_list_unread_audit_spam_path(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
        )
        messages = [
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "buy now",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Spammer <spam@x.com>"},
                        {"name": "Subject", "value": "Sale"},
                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                    ]
                },
            }
        ]
        with patch("main.list_messages", return_value=messages), patch(
            "main.input", side_effect=["s"]
        ), patch("main.delete_message") as delete_mock, patch(
            "main.update_account_sender_lists"
        ) as update_mock:
            code = _handle_list(
                service,
                ["-ura", "-l", "1"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        delete_mock.assert_called_once_with(service, "m1")
        update_payload = update_mock.call_args.args[1]
        self.assertIn("spam@x.com", update_payload["1"])

    def test_handle_list_unread_audit_no_limit_uses_batches(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
        )
        with patch("main.list_messages_page", return_value=([], None) ) as list_page_mock, patch(
            "main.update_account_sender_lists"
        ):
            code = _handle_list(
                service,
                ["-ura"],
                default_limit=10,
                my_email="me@example.com",
                config_path=Path("/tmp/config.json"),
                account=account,
            )
        self.assertEqual(code, 0)
        list_page_mock.assert_called_once_with(
            service,
            "is:unread -in:sent",
            max_results=10,
            page_token=None,
        )

    def test_handle_list_unread_audit_trash_only(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
        )
        messages = [
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "offer",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Sender <sender@x.com>"},
                        {"name": "Subject", "value": "Offer"},
                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                    ]
                },
            }
        ]
        with patch("main.list_messages", return_value=messages), patch(
            "main.input", side_effect=["t"]
        ), patch("main.delete_message") as delete_mock, patch(
            "main.update_account_sender_lists"
        ) as update_mock:
            code = _handle_list(
                service,
                ["-ura", "-l", "1"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        delete_mock.assert_called_once_with(service, "m1")
        update_payload = update_mock.call_args.args[1]
        self.assertEqual(update_payload["1"], [])

    def test_handle_list_unread_audit_gmail_sender_protected(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
        )
        messages = [
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "hello",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Person <person@gmail.com>"},
                        {"name": "Subject", "value": "Hi"},
                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                    ]
                },
            }
        ]
        with patch("main.list_messages", return_value=messages), patch(
            "main.input", side_effect=["t"]
        ), patch("main.delete_message") as delete_mock, patch(
            "main.update_account_sender_lists"
        ) as update_mock:
            code = _handle_list(
                service,
                ["-ura", "-l", "1"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        delete_mock.assert_not_called()
        update_payload = update_mock.call_args.args[1]
        self.assertEqual(update_payload["1"], [])

    def test_handle_list_read_audit_custom_limit(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
        )
        with patch("main.list_messages", return_value=[] ) as list_messages_mock:
            code = _handle_list(
                service,
                ["-ra", "-l", "5"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "is:read -in:sent", 5)


if __name__ == "__main__":
    unittest.main()
    def test_handle_list_external_default_unbounded(self) -> None:
        service = MagicMock()
        with patch("main.list_all_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            code = _handle_list(service, ["-ext"], default_limit=10, my_email="me@example.com")
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(
            service, "-from:me@example.com -from:*@example.com"
        )
