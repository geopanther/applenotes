"""Environment, optional dotenv, and explicit configuration."""

import json
from string import Formatter
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from appletextnotes.models import Identity

DEFAULT_MERGE_COMMAND = [
    "git",
    "merge-file",
    "--stdout",
    "--diff3",
    "{local}",
    "{base}",
    "{remote}",
]


def validate_command(command: list[str]) -> list[str]:
    if not command or not command[0]:
        raise ValueError("merge command must contain an executable")
    found: set[str] = set()
    for arg in command:
        if "\x00" in arg:
            raise ValueError("invalid command argument")
        for _, name, spec, conversion in Formatter().parse(arg):
            if name is not None:
                if name not in {"local", "base", "remote"} or spec or conversion:
                    raise ValueError("unknown merge placeholder")
                found.add(name)
    if found != {"local", "base", "remote"}:
        raise ValueError("merge command needs local, base, and remote placeholders")
    return command


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APPLETEXTNOTES_", env_file=".env", extra="ignore")

    imap_server: str | None = None
    imap_username: str | None = None
    imap_password: SecretStr | None = Field(default=None, exclude=True, repr=False)
    imap_folder: str = "Notes"
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security: Literal["tls", "starttls"] = "tls"
    timeout_seconds: float = Field(default=30, gt=0)
    indent_spaces: int = Field(default=4, ge=1, le=32)
    merge_command: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: DEFAULT_MERGE_COMMAND.copy()
    )
    merge_timeout_seconds: float = Field(default=30, gt=0)

    def __init__(self, **values):
        super().__init__(**values)
        if self.imap_port is None:
            self.imap_port = 993 if self.imap_security == "tls" else 143

    @field_validator("merge_command", mode="before")
    @classmethod
    def parse_command(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON: {error.msg}") from error
        return value

    @field_validator("merge_command")
    @classmethod
    def check_command(cls, value: list[str]) -> list[str]:
        return validate_command(value)

    @field_validator("imap_server", "imap_username", "imap_folder")
    @classmethod
    def check_header_safety(cls, value: str | None) -> str | None:
        if value is not None and (not value or any(c in value for c in "\r\n\x00")):
            raise ValueError("invalid connection setting")
        return value

    def require_network(self) -> None:
        missing = [
            f"APPLETEXTNOTES_{field.upper()}"
            for field in ("imap_server", "imap_username", "imap_password")
            if not getattr(self, field)
        ]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")

    def identity(self) -> Identity:
        self.require_network()
        assert self.imap_server is not None and self.imap_username is not None
        assert self.imap_port is not None
        return Identity(
            server=self.imap_server.casefold(),
            username=self.imap_username,
            folder=self.imap_folder,
            port=self.imap_port,
            security=self.imap_security,
        )
