"""The settings of the lab sources, from the `[sources.frr]` and `[sources.srlinux]` tables of snapdelta.toml."""

from pathlib import Path

from pydantic import Field
from snapdelta.settings import SourceSettings


class RouterSettings(SourceSettings):
    """`timeout_s` limits each router command; `outputs` reads saved outputs instead of the live lab."""

    timeout_s: float = Field(default=30, gt=0)
    # One directory per source, then one per router, as `snapdelta-lab-capture` writes them.
    outputs: Path | None = None


def router_settings(settings: SourceSettings | None) -> RouterSettings:
    """The settings that snapdelta gave, else the defaults."""
    return settings if isinstance(settings, RouterSettings) else RouterSettings()
