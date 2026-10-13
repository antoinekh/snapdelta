"""The common part of the lab sources: the models, the settings, the targets and the steps of a load."""

from abc import abstractmethod
from typing import ClassVar

from snapdelta.errors import LoadError
from snapdelta.inputs import InputFile
from snapdelta.settings import SourceSettings
from snapdelta.source import InputRef, Source

from .commands import DockerRunner, Runner, SavedRunner, lab_targets
from .models import BgpPeer, BgpRoute, Interface
from .settings import RouterSettings, router_settings

MODELS = ("interface", "bgp_peer", "bgp_route")


class LabSource(Source):
    """One router of the lab; with `outputs` in its settings, the saved outputs of that router instead."""

    distribution = "snapdelta-lab"
    fetches = True
    settings_class = RouterSettings

    interface = Interface
    bgp_peer = BgpPeer
    bgp_route = BgpRoute
    top_level = ["interface", "bgp_peer"]

    # The containerlab kind of the routers of this source, and the command line that runs one CLI command.
    kind: ClassVar[str]
    shell: ClassVar[tuple[str, ...]]
    # Output keys that a capture leaves out: counters and host names, which change with each deployment.
    noise: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def targets(cls, settings: SourceSettings | None = None) -> list[str]:
        """The routers that `--all-targets` and the web page "Take snapshot" offer."""
        chosen = router_settings(settings)
        if chosen.outputs is not None:
            saved = chosen.outputs / cls.source
            return sorted(path.name for path in saved.iterdir() if path.is_dir()) if saved.is_dir() else []
        return lab_targets(cls.kind, chosen.timeout_s)

    @classmethod
    def runner(cls, target: str, settings: SourceSettings | None, container: str | None = None) -> Runner:
        """The saved outputs when the settings name them, else the running container (the inventory can name it)."""
        chosen = router_settings(settings)
        if chosen.outputs is not None:
            return SavedRunner(target, chosen.outputs / cls.source / target)
        return DockerRunner(target, chosen.timeout_s, cls.shell, container)

    @classmethod
    @abstractmethod
    def fetch_outputs(cls, runner: Runner) -> dict[str, InputFile]:
        """Every output of one router, by key."""

    @abstractmethod
    def fill(self, outputs: dict[str, InputFile]) -> None:
        """Parse the outputs and add the models."""

    def load(self) -> None:
        """Fetch the outputs of `self.target`, fill the models, then record the load with `finish_load()`."""
        if not self.target:
            raise LoadError(self.source, "no target name: give the router name, for example r1")
        # With an inventory, its hostname is the container of the router.
        container = self.host.hostname if self.host else None
        outputs = self.fetch_outputs(self.runner(self.target, self.settings, container))
        self.inputs.extend(InputRef(name=output.name, sha256=output.sha256) for output in outputs.values())
        self.fill(outputs)
        self.finish_load(self.target, MODELS)
