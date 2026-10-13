"""An input file of a source: its name, its content and its hash."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import LoadError


@dataclass(frozen=True)
class InputFile:
    name: str
    content: bytes

    @classmethod
    def from_path(cls, path: Path) -> InputFile:
        try:
            return cls(name=path.name, content=path.read_bytes())
        except OSError as error:
            raise LoadError(str(path), f"cannot read the file ({error.strerror})") from error

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()
