"""The source `example-live`: it fetches the data of a target itself, with no input file.

    snapdelta snapshot --source example-live --target R1
    snapdelta snapshot --source example-live --all-targets

The devices are simulated: one JSON file per device in `devices/`. A real source connects to the device here
(NETCONF, SSH, an API) and keeps the same structure: fetch, fill the models, then `finish_load()`.
"""

import logging
from pathlib import Path

from snapdelta.errors import LoadError
from snapdelta.inputs import InputFile
from snapdelta.settings import SourceSettings
from snapdelta.source import InputRef

from .source import ExampleSource, parse_json

# The simulated devices of the example, next to the package.
DEVICES = Path(__file__).resolve().parents[2] / "devices"

# snapdelta saves the log messages of each load with its snapshot: Activity and the snapshot page show them.
log = logging.getLogger(__name__)


class ExampleLiveSettings(SourceSettings):
    """The settings of the source, from the table `[sources.example-live]` of snapdelta.toml.

    snapdelta checks the types, shows the values in `snapdelta settings`, and edits them in Admin > Settings.
    """

    # Another directory of simulated devices; a real source would hold here a URL, a timeout, or a password
    # (a `snapdelta.settings.Secret`).
    data_dir: Path | None = None


class ExampleLiveSource(ExampleSource):
    """Fetches one device; a subclass of ExampleSource, so it has the same models and its snapshots compare."""

    source = "example-live"
    # True: load() gets the data of `self.target` itself; snapdelta can then take several targets at the same time
    # (`--all-targets`, `--jobs`) and compare a snapshot with the device now (`diff --live`).
    fetches = True
    # The class of `self.settings`: snapdelta reads `[sources.example-live]` into it before load().
    settings_class = ExampleLiveSettings

    @classmethod
    def targets(cls, settings: SourceSettings | None = None) -> list[str]:
        """The targets that the source can reach: the web UI lists them, and `--all-targets` takes them all."""
        return sorted(path.stem for path in devices_dir(settings).glob("*.json"))

    def load(self) -> None:
        # snapdelta sets the target name before load(); with an inventory, `self.host` also gives its address,
        # port and login, which this simulation does not need.
        if not self.target:
            raise LoadError(self.source, "no target name: give the target name")
        folder = devices_dir(self.settings)
        path = folder / f"{self.target}.json"
        if not path.is_file():
            raise LoadError(self.target, f"not reachable (no {path.name} in {folder})")
        log.info("read %s from %s", path.name, folder)
        fetched = InputFile(name=path.name, content=path.read_bytes())
        # Record what was fetched, as read_inputs() does for a file, so that the snapshot says what it was made from.
        self.inputs.append(InputRef(name=fetched.name, sha256=fetched.sha256))
        loaded = self.fill(parse_json(fetched))
        self.finish_load(self.target, loaded)


def devices_dir(settings: SourceSettings | None) -> Path:
    """The directory of the simulated devices: `data_dir` of the settings, else `devices/` of the example."""
    chosen = settings if isinstance(settings, ExampleLiveSettings) else ExampleLiveSettings()
    return chosen.data_dir or DEVICES
