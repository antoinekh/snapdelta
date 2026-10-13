"""The `snapdelta-lab-capture` command: save the outputs of the running routers, for the tests and the saved mode."""

import argparse
import json
from pathlib import Path

from snapdelta.settings import load_settings

from .base import LabSource
from .commands import DockerRunner, json_of, without_keys
from .frr import FrrSource
from .settings import RouterSettings
from .srlinux import SrlinuxSource

SOURCES: tuple[type[LabSource], ...] = (FrrSource, SrlinuxSource)


def capture(cls: type[LabSource], target: str, directory: Path, timeout_s: float) -> list[Path]:
    """Save the outputs of one running router, without the keys that change with each deployment."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for key, output in cls.fetch_outputs(DockerRunner(target, timeout_s, cls.shell)).items():
        path = directory / f"{key}.json"
        path.write_text(json.dumps(without_keys(json_of(output), cls.noise), indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Save the JSON outputs of the lab routers: DIRECTORY/SOURCE/ROUTER.")
    parser.add_argument("directory", type=Path, help="Output directory.")
    args = parser.parse_args()
    for cls in SOURCES:
        # A capture always reads the live lab, whatever the settings say about saved outputs.
        timeout_s = _timeout(cls)
        for target in cls.targets(RouterSettings(timeout_s=timeout_s)):
            for path in capture(cls, target, args.directory / cls.source / target, timeout_s):
                print(path)


def _timeout(cls: type[LabSource]) -> float:
    settings = load_settings(installed={cls.source: cls}).source(cls)
    return settings.timeout_s if isinstance(settings, RouterSettings) else RouterSettings().timeout_s
