"""snapdelta lab plugin: two sources, `frr` and `srlinux`, fill the same models from two vendors.

A load has three steps: the source runs the commands of one router (in parallel), parses their JSON outputs
into the models of `models`, then records the load. The entry points `frr` and `srlinux` in `pyproject.toml`
make the sources known to snapdelta (`snapdelta sources`).
"""

from .frr import FrrSource
from .srlinux import SrlinuxSource

__all__ = ["FrrSource", "SrlinuxSource"]
