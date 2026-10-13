"""The JSON files of snapdelta: atomic write, and read with a schema version check."""

import json
import tempfile
from pathlib import Path
from typing import Any

from .errors import NotFoundError, SchemaVersionError, SnapdeltaError


def write_json(path: Path, data: str) -> None:
    """Write through a temporary file and a rename, so that an interrupted write never leaves a broken file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def read_json(path: Path, schema: int) -> dict[str, Any]:
    """The JSON document of `path`, which must hold `schema`."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise NotFoundError(f"{path}: no such file") from error
    except json.JSONDecodeError as error:
        raise SnapdeltaError(f"{path}: not a valid JSON file ({error})") from error
    found = value.get("schema") if isinstance(value, dict) else None
    if found != schema:
        raise SchemaVersionError(f"{path}: schema {found!r}, this snapdelta reads schema {schema}")
    return value
