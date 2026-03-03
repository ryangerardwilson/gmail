from __future__ import annotations


class GmailCliError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class UsageError(GmailCliError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class ConfigError(GmailCliError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=3)


class ApiError(GmailCliError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=4)
