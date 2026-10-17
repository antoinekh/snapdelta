"""Secret backends: where a `Secret` of the settings reads its value, `{ BACKEND = "REFERENCE" }` in the file.

The built-in backends are `env` (an environment variable) and `file` (a file, such as a Docker or Kubernetes
secret); a plugin adds others in the entry point group `snapdelta.secrets`, such as a vault or the system keyring.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from .plugins import ExtensionPoint, Problem, abstract_problems, settings_problems, subclass_check
from .settings import SecretBackendSettings, Settings, SettingsError

# The keys of the table form of a Secret, which no backend can take as its name.
RESERVED = ("plain", "backend", "reference")

_settings_in_use: list[Settings] = []


class SecretError(SettingsError):
    """A secret that its backend cannot read."""


class SecretBackend(ABC):
    """A place that holds secrets: it reads one by its reference, with its `[secrets.NAME]` settings."""

    settings_class: ClassVar[type[SecretBackendSettings]] = SecretBackendSettings

    @classmethod
    @abstractmethod
    def read(cls, reference: str, settings: SecretBackendSettings) -> str:
        """The value of the secret; raise SecretError when the backend has no such secret."""


class EnvSecrets(SecretBackend):
    """`{ env = "NAME" }`: the value of an environment variable."""

    @classmethod
    def read(cls, reference: str, settings: SecretBackendSettings) -> str:
        found = os.environ.get(reference)
        if found is None:
            raise SecretError(f"environment variable {reference} is not set")
        return found


class FileSecrets(SecretBackend):
    """`{ file = "/run/secrets/NAME" }`: the content of a file, without its last line break."""

    @classmethod
    def read(cls, reference: str, settings: SecretBackendSettings) -> str:
        path = Path(reference).expanduser()
        if not path.is_absolute():
            # The current directory of a server is not a place that a reader of the settings file can see.
            raise SecretError(f"secret file {reference}: give an absolute path")
        try:
            return path.read_text(encoding="utf-8").removesuffix("\n").removesuffix("\r")
        except OSError as error:
            raise SecretError(f"secret file {path}: {error.strerror}") from error


def _check_backend(name: str, value: Any) -> str | None:
    if name in RESERVED:
        return f"{name!r} is a key of the table of a secret: give the backend another name"
    return subclass_check(SecretBackend)(name, value)


def _inspect_backend(name: str, cls: type[SecretBackend]) -> list[Problem]:
    return abstract_problems(cls) + settings_problems(cls, SecretBackendSettings)


SECRETS: ExtensionPoint[type[SecretBackend]] = ExtensionPoint(
    group="snapdelta.secrets",
    kind="secret backend",
    interface="snapdelta.secrets.SecretBackend",
    table="secrets",
    check=_check_backend,
    inspect=_inspect_backend,
)


def use_settings(settings: Settings) -> None:
    """The settings whose `[secrets.NAME]` tables the backends read; load_settings() calls it."""
    _settings_in_use[:] = [settings]


def backend_settings(name: str, cls: type[SecretBackend]) -> SecretBackendSettings:
    """The settings of one backend, from the settings in use, or its defaults."""
    if _settings_in_use:
        return _settings_in_use[0].plugin("secrets", name, cls.settings_class)
    return cls.settings_class()
