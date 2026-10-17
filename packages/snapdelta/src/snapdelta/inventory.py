"""An optional inventory of hosts: their groups, their platform (which chooses the source) and their connection data."""

from __future__ import annotations

import stat
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, ClassVar

import yaml

from .errors import SnapdeltaError
from .settings import InventoryFormatSettings

INVENTORY_GROUP = "snapdelta.inventories"
# The keys of a Nornir host, group or defaults entry, besides `groups` and `data`.
NORNIR_KEYS = ("hostname", "port", "username", "password", "platform")


class InventoryError(SnapdeltaError):
    """An inventory that cannot be read, or a host or a group that it does not hold."""


@dataclass(frozen=True)
class Host:
    """One host of the inventory, with the values of its groups and of the defaults already applied."""

    name: str
    hostname: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    platform: str | None = None
    # Its groups, then their parent groups, in the order that Nornir looks them up.
    groups: tuple[str, ...] = ()
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Inventory:
    hosts: dict[str, Host]
    groups: list[str]
    location: str
    warnings: list[str] = field(default_factory=list)

    def host(self, name: str) -> Host | None:
        return self.hosts.get(name)

    def select(self, names: Iterable[str] = (), groups: Iterable[str] = ()) -> list[str]:
        """The host names of `names` and of the members of `groups`, without duplicates, in the given order."""
        selected = list(dict.fromkeys(name for name in names if name))
        for group in groups:
            selected += self.members(group)
        return list(dict.fromkeys(selected))

    def members(self, group: str) -> list[str]:
        """The hosts of a group, its subgroups included, sorted."""
        if group not in self.groups:
            known = ", ".join(self.groups) or "none"
            raise InventoryError(f"no group {group!r} in the inventory (groups: {known})")
        return sorted(name for name, host in self.hosts.items() if group in host.groups)


class InventoryFormat(ABC):
    """A format of inventory: it reads the hosts at `[inventory] location`, with its `[inventories.NAME]` settings."""

    # True: the location is a path on this machine, so the settings file resolves it against its directory.
    local_path: ClassVar[bool] = False
    # The typed settings that snapdelta reads for this format from the [inventories.NAME] table.
    settings_class: ClassVar[type[InventoryFormatSettings]] = InventoryFormatSettings

    @classmethod
    @abstractmethod
    def load(cls, location: str, settings: InventoryFormatSettings) -> Inventory:
        """The inventory at `location`: a directory, a file or a URL, as the format reads it."""


class NornirInventory(InventoryFormat):
    local_path = True

    @classmethod
    def load(cls, location: str, settings: InventoryFormatSettings) -> Inventory:
        return load_nornir(Path(location))


def inventory_formats() -> dict[str, type[InventoryFormat]]:
    """The inventory formats by name: Nornir, then the formats of the installed plugins."""
    found: dict[str, type[InventoryFormat]] = {"nornir": NornirInventory}
    for entry in entry_points(group=INVENTORY_GROUP):
        loaded = entry.load()
        if isinstance(loaded, type) and issubclass(loaded, InventoryFormat):
            found[entry.name] = loaded
    return found


def inventory_format(name: str) -> type[InventoryFormat]:
    formats = inventory_formats()
    if name not in formats:
        raise InventoryError(f"no inventory format {name!r} (formats: {', '.join(sorted(formats))})")
    return formats[name]


def load_nornir(location: Path) -> Inventory:
    """A Nornir SimpleInventory: `hosts.yaml`, and the optional `groups.yaml` and `defaults.yaml` of one directory."""
    hosts_file = location / "hosts.yaml"
    if not hosts_file.is_file():
        raise InventoryError(f"{location}: no hosts.yaml (a Nornir inventory is a directory with hosts.yaml)")
    raw_hosts = _checked_entries(hosts_file, _yaml_mapping(hosts_file))
    raw_groups = _checked_entries(location / "groups.yaml", _yaml_mapping(location / "groups.yaml"))
    defaults = _yaml_mapping(location / "defaults.yaml")
    hosts = {str(name): _nornir_host(str(name), entry, raw_groups, defaults) for name, entry in raw_hosts.items()}
    warnings = []
    has_password = any(host.password for host in hosts.values())
    files = [path for path in (hosts_file, location / "groups.yaml", location / "defaults.yaml") if path.is_file()]
    if has_password and any(path.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH) for path in files):
        warnings.append(f"{location} holds passwords and other users can read it: chmod 600 {location}/*.yaml")
    return Inventory(hosts, sorted(str(name) for name in raw_groups), str(location), warnings)


def _yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise InventoryError(f"{path}: not valid YAML ({error})") from error
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InventoryError(f"{path}: the file must hold a mapping of names to entries")
    return value


def _checked_entries(path: Path, value: dict[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The entries of hosts.yaml or groups.yaml: each a mapping of keys to values, never a bare value."""
    entries: dict[str, Mapping[str, Any]] = {}
    for name, entry in value.items():
        if entry is not None and not isinstance(entry, Mapping):
            raise InventoryError(f"{path}: the entry of {name!r} must be a mapping of keys to values, not {entry!r}")
        entries[name] = entry or {}
    return entries


def _nornir_host(
    name: str, entry: Mapping[str, Any], groups: Mapping[str, Mapping[str, Any]], defaults: Mapping[str, Any]
) -> Host:
    lineage = _lineage(name, _group_names(name, entry), groups)
    # Nornir looks a value up in the host, then in its groups in order, then in the defaults.
    layers = [entry, *(groups[group] for group in lineage), defaults]
    values = {key: next((layer[key] for layer in layers if layer.get(key) is not None), None) for key in NORNIR_KEYS}
    data: dict[str, Any] = {}
    for layer in reversed(layers):
        found = layer.get("data")
        if found is not None and not isinstance(found, Mapping):
            raise InventoryError(f"{name}: data must be a mapping of keys to values, not {found!r}")
        data |= found or {}
    port = values["port"]
    try:
        number = int(port) if port is not None else None
    except (TypeError, ValueError, OverflowError) as error:
        raise InventoryError(f"{name}: the port {port!r} is not a number") from error
    return Host(
        name=name,
        hostname=_text(values["hostname"]),
        port=number,
        username=_text(values["username"]),
        password=_text(values["password"]),
        platform=_text(values["platform"]),
        groups=tuple(lineage),
        data=data,
    )


def _group_names(owner: str, entry: Mapping[str, Any]) -> list[str]:
    """The `groups` of a host or group entry; a string is a mistake of the file, not a list of one group."""
    value = entry.get("groups")
    if value is None:
        return []
    if not isinstance(value, list):
        raise InventoryError(f"{owner}: groups must be a list of group names, not {value!r}")
    return [str(name) for name in value]


def _lineage(owner: str, direct: Iterable[str], groups: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The groups of an entry, each followed by its own parent groups, depth first, without repeats."""
    found: list[str] = []

    def visit(group: str) -> None:
        if group in found:
            return
        if group not in groups:
            raise InventoryError(f"{owner}: group {group!r} is not in groups.yaml")
        found.append(group)
        for parent in _group_names(group, groups[group]):
            visit(parent)

    for group in direct:
        visit(group)
    return found


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
