"""Run the commands of one lab router in its container, or read the outputs that a capture saved."""

import functools
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

from snapdelta.errors import LoadError
from snapdelta.inputs import InputFile
from snapdelta.parallel import fetch_parallel

LAB = "snapdelta-lab"
# snapdelta saves these messages with the snapshot of each router: Activity and the snapshot page show them.
log = logging.getLogger(__name__)


class Runner(Protocol):
    """Runs one command of a router; `key` names the output (`bgp`, `routes-10.12.10.2`, ...)."""

    def run(self, key: str, command: str) -> InputFile: ...


class DockerRunner:
    """Runs a command in the containerlab container of the router, through the CLI that `shell` starts."""

    def __init__(self, target: str, timeout_s: float, shell: tuple[str, ...], container: str | None = None) -> None:
        self.target = target
        self.timeout_s = timeout_s
        self.shell = shell
        self.container = container or f"clab-{LAB}-{target}"

    def run(self, key: str, command: str) -> InputFile:
        start = time.monotonic()
        try:
            done = subprocess.run(
                ["docker", "exec", self.container, *self.shell, command],
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LoadError(self.target, f"{command}: {error}") from error
        if done.returncode != 0:
            # docker exec gives the error of a missing program on stdout, with an empty stderr.
            reason = (done.stderr.strip() or done.stdout.strip()).decode(errors="replace")
            raise LoadError(self.target, f"{command}: exit {done.returncode}" + (f": {reason}" if reason else ""))
        log.info("%s: %d bytes in %.2f s", command, len(done.stdout), time.monotonic() - start)
        return InputFile(name=f"{key}.json", content=done.stdout)


class SavedRunner:
    """Reads the outputs that `snapdelta-lab-capture` saved: one JSON file per command."""

    def __init__(self, target: str, directory: Path) -> None:
        self.target = target
        self.directory = directory

    def run(self, key: str, command: str) -> InputFile:
        log.info("%s: the saved output %s", command, self.directory / f"{key}.json")
        return InputFile.from_path(self.directory / f"{key}.json")


def run_all(runner: Runner, commands: dict[str, str]) -> dict[str, InputFile]:
    """The outputs of `commands`, by key, run at the same time."""
    return fetch_parallel({key: functools.partial(runner.run, key, command) for key, command in commands.items()})


def lab_targets(kind: str, timeout_s: float) -> list[str]:
    """The running routers of the lab whose containerlab kind is `kind`."""
    labels = ["--filter", f"label=containerlab={LAB}", "--filter", f"label=clab-node-kind={kind}"]
    command = ["docker", "ps", *labels, "--format", '{{.Label "clab-node-name"}}']
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted(name for name in done.stdout.split() if name)


def json_of(output: InputFile) -> dict[str, Any]:
    """The JSON object of one output; raises LoadError, which names the output, when it is not JSON."""
    try:
        value = json.loads(output.content)
    except json.JSONDecodeError as error:
        raise LoadError(output.name, f"not JSON: {error}") from error
    if not isinstance(value, dict):
        raise LoadError(output.name, "not a JSON object")
    return value


def without_keys(value: Any, keys: frozenset[str]) -> Any:
    """`value` without `keys`, at any depth."""
    if isinstance(value, dict):
        return {key: without_keys(item, keys) for key, item in value.items() if key not in keys}
    if isinstance(value, list):
        return [without_keys(item, keys) for item in value]
    return value
