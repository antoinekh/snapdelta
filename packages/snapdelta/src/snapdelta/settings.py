"""The settings of snapdelta: one TOML file, typed by pydantic models; an option or an environment variable wins."""

from __future__ import annotations

import os
import re
import stat
import tempfile
import tomllib
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import tomlkit
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from tomlkit.container import Container
from tomlkit.items import Comment, Item, Key, Null, Table, Whitespace

from .errors import SnapdeltaError

if TYPE_CHECKING:
    from .inventory import Inventory
    from .source import Source

FILE_NAME = "snapdelta.toml"
SETTINGS_ENV = "SNAPDELTA_SETTINGS"
STORE_ENV = "SNAPDELTA_STORE"
CORE_SECTIONS = ("store", "snapshot", "web", "inventory")


class SettingsError(SnapdeltaError):
    """A settings file that cannot be read or that holds a bad value; `errors` maps each dotted key to its message."""

    def __init__(self, message: str, errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or {}


class Origin(StrEnum):
    DEFAULT = "default"
    FILE = "file"
    ENV = "env"
    OPTION = "option"


class Section(BaseModel):
    # A typo in the file must fail loudly, never fall back to a default in silence.
    model_config = ConfigDict(extra="forbid")


class StoreSettings(Section):
    location: str | None = None


class StoreBackendSettings(Section):
    """The base of the settings of a store backend; a backend subclass adds its typed fields."""


class SnapshotSettings(Section):
    jobs: int = Field(default=4, ge=1)
    runner: str = Field(
        default="threads",
        description="How the loads run: threads, processes (one process per load), or a runner of a plugin.",
    )
    log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info",
        description="The log messages of a load at this level and above are saved with its snapshot and its run.",
    )


class InventorySettings(Section):
    format: str = Field(default="nornir", description="The format of the inventory: nornir, or a format of a plugin.")
    location: str | None = Field(
        default=None,
        description=(
            "The inventory, as its format reads it: for nornir, the directory of hosts.yaml, groups.yaml and "
            "defaults.yaml; a URL for a format such as NetBox."
        ),
        examples=["inventory"],
    )
    platforms: dict[str, str] = Field(
        default={},
        description=(
            "The source of the hosts of each platform, when its name is not the name of the platform: "
            "one platform = source per line."
        ),
        examples=["junos = junos-netconf\nsros = sros-netconf"],
    )


class SourceSettings(Section):
    """The base of the settings of a source; a plugin subclass adds its typed fields."""


class InventoryFormatSettings(Section):
    """The base of the settings of an inventory format; a plugin subclass adds its typed fields."""


SECRET_FORM = 'give a value, or a table with one key, its backend: { env = "NAME" }'


class SecretBackendSettings(Section):
    """The base of the settings of a secret backend; a plugin subclass adds its typed fields."""


class Secret(BaseModel):
    """A password or a token: a plain value, or a table with one key, the backend that reads it: `{ env = "NAME" }`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: str | None = None
    reference: str | None = None
    plain: str | None = Field(default=None, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _from_toml(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"plain": value}
        if isinstance(value, Mapping) and not set(value) <= {"plain", "backend", "reference"}:
            if len(value) != 1:
                raise ValueError(SECRET_FORM)
            [(backend, reference)] = value.items()
            return {"backend": backend, "reference": reference}
        return value

    @model_validator(mode="after")
    def _one_of(self) -> Secret:
        if self.plain is not None and self.backend is None and self.reference is None:
            return self
        if self.plain is not None or not self.backend or not self.reference:
            raise ValueError(SECRET_FORM)
        from .plugins import discover
        from .secrets import SECRETS

        found = discover(SECRETS)
        if self.backend not in found.plugins:
            installed = ", ".join(sorted(found.plugins)) or "none"
            raise ValueError(f"no secret backend {self.backend!r} is installed (installed: {installed})")
        return self

    @property
    def is_plain(self) -> bool:
        return self.plain is not None

    def value(self) -> str:
        """The secret, read from its backend at each call, so that a changed secret needs no restart."""
        if self.plain is not None:
            return self.plain
        from .plugins import require
        from .secrets import SECRETS, backend_settings

        backend, reference = self.backend or "", self.reference or ""
        cls = require(SECRETS, backend)
        try:
            return cls.read(reference, backend_settings(backend, cls))
        except SnapdeltaError:
            raise
        except Exception as error:
            # A bug or an outage of a plugin backend: the message must still name the secret.
            raise SettingsError(f"secret {self.describe()}: {type(error).__name__}: {error}") from error

    def describe(self) -> str:
        return f"{self.backend} {self.reference}" if self.backend else "********"

    def to_toml(self) -> str | dict[str, str]:
        return self.plain if self.plain is not None else {self.backend or "": self.reference or ""}


class WebSettings(Section):
    host: str = Field(
        default="127.0.0.1",
        description="The address that snapdelta-web listens on; any other address needs a token, or auth none.",
    )
    port: int = Field(default=8765, ge=1, le=65535)
    token: Secret | None = Field(
        default=None,
        description="When set, the web pages ask for it once (login) and the API needs it as a Bearer token.",
    )
    tls_cert: Path | None = Field(default=None, description="The certificate file for HTTPS, with tls_key.")
    tls_key: Path | None = Field(default=None, description="The private key file for HTTPS, with tls_cert.")
    auth: str = Field(
        default="token",
        description=(
            "token: a login when a token is set, and a token on the network. "
            "header: the user that a trusted reverse proxy gives in a header (SSO). "
            "none: no login for the pages and the API, on any address, for an isolated network only. "
            "Or the backend of a plugin, such as LDAP or OIDC."
        ),
    )
    public_url: str | None = Field(
        default=None,
        description="The address of the web UI that a person opens, for the links of the notifiers.",
        examples=["https://snapdelta.example.net"],
    )
    allowed_hosts: list[str] = Field(
        default=[],
        description=(
            "The host names of the server that a browser may use. With a token, empty means any; "
            'with auth none, they are required on the network ("*": any).'
        ),
        examples=["snapdelta.example.net"],
    )


PERCENT_RE = re.compile(r"^\d+(?:\.\d+)?%$")
NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
# A profile name is one part of a TOML table path and of a URL.
PROFILE_NAME_RE = re.compile(r"^[\w-]+$")


def _limit(value: float | str) -> float | str:
    if isinstance(value, str) and NUMBER_RE.match(value):
        # A form field gives text: "10" is the number 10, as in the file.
        value = float(value)
    if isinstance(value, str) and not PERCENT_RE.match(value):
        raise ValueError('give a number, or a percent such as "5%"')
    if isinstance(value, int | float) and value < 0:
        raise ValueError("a limit cannot be negative")
    return value


def _attribute_pattern(value: str) -> str:
    if value.count(".") != 1:
        raise ValueError(f'{value!r}: write "model.attribute"; * matches any part')
    return value


def _rule_name(value: str) -> str:
    from .plugins import discover
    from .rules import RULES

    found = discover(RULES).plugins
    if value not in found:
        raise ValueError(f"no compare rule {value!r} is installed (installed: {', '.join(sorted(found)) or 'none'})")
    return value


def _profile_name(value: str) -> str:
    if not PROFILE_NAME_RE.match(value):
        raise ValueError(f"{value!r}: a profile name holds letters, digits, _ and - only")
    return value


# A number is an absolute limit; "N%" is relative to the left value.
Limit = Annotated[float | str, AfterValidator(_limit)]
ProfileName = Annotated[str, AfterValidator(_profile_name)]
AttributePattern = Annotated[str, AfterValidator(_attribute_pattern)]
RuleName = Annotated[str, AfterValidator(_rule_name)]


class Profile(Section):
    """The rules of a compare: what to ignore, what may differ a little, and which names pair across targets."""

    # The descriptions and the examples are the help of the web settings form.
    description: str | None = Field(
        default=None,
        max_length=120,
        pattern=r"^[^\n]*$",
        description="One line that says what the profile is for; the profile lists show it next to the name.",
        examples=["Change window, descriptions ignored, MTU within 10%"],
    )
    ignore: list[AttributePattern] = Field(
        default=[],
        description="Attributes whose differences do not count: one model.attribute per line; * matches any part.",
        examples=["interface.description\n*.mtu"],
    )
    tolerance: dict[AttributePattern, Limit] = Field(
        default={},
        description=(
            "Numbers that may differ by a limit: one model.attribute = limit per line. "
            "A number is an absolute limit; N% is a percent of the left value (1500 to 1650 is within 10%)."
        ),
        examples=["interface.mtu = 10%\nbgp_peer.prefixes_received = 50"],
    )
    rules: dict[AttributePattern, RuleName] = Field(
        default={},
        description=(
            "Values that differ as written but mean the same: one model.attribute = rule per line. "
            "Rules: case-insensitive, whitespace, unordered, ip-address, and the rules of plugins."
        ),
        examples=["interface.description = case-insensitive\ninterface.addresses = ip-address"],
    )
    counts: dict[str, Limit] = Field(
        default={},
        description=(
            "An accepted change of the number of objects of a model: one model = limit per line. "
            "Within the limit, the objects of one side only of that model are not shown."
        ),
        examples=["bgp_route = 5%"],
    )
    pairs: dict[str, str] = Field(
        default={},
        description=(
            "Names that are the same object on the two sides: one left = right per line. "
            "The right side takes the left names, in identifiers and in values."
        ),
        examples=["ge-0/0/6 = 1/1/1"],
    )
    skip_only_left: bool = Field(default=False, description="Do not compare the objects that only the left side has.")
    skip_only_right: bool = Field(default=False, description="Do not compare the objects that only the right side has.")


class FileSettings(Section):
    store: StoreSettings = StoreSettings()
    snapshot: SnapshotSettings = SnapshotSettings()
    web: WebSettings = WebSettings()
    inventory: InventorySettings = InventorySettings()
    sources: dict[str, dict[str, Any]] = {}
    stores: dict[str, dict[str, Any]] = {}
    inventories: dict[str, dict[str, Any]] = {}
    secrets: dict[str, dict[str, Any]] = {}
    notifiers: dict[str, dict[str, Any]] = {}
    runners: dict[str, dict[str, Any]] = {}
    auth: dict[str, dict[str, Any]] = {}
    profiles: dict[ProfileName, Profile] = {}


@dataclass
class Settings:
    store: StoreSettings = field(default_factory=StoreSettings)
    snapshot: SnapshotSettings = field(default_factory=SnapshotSettings)
    web: WebSettings = field(default_factory=WebSettings)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    file: Path | None = None
    origins: dict[str, Origin] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    profiles: dict[str, Profile] = field(default_factory=dict)
    inventory: InventorySettings = field(default_factory=InventorySettings)
    stores: dict[str, dict[str, Any]] = field(default_factory=dict)
    inventories: dict[str, dict[str, Any]] = field(default_factory=dict)
    secrets: dict[str, dict[str, Any]] = field(default_factory=dict)
    notifiers: dict[str, dict[str, Any]] = field(default_factory=dict)
    runners: dict[str, dict[str, Any]] = field(default_factory=dict)
    auth: dict[str, dict[str, Any]] = field(default_factory=dict)
    _plugin_cache: dict[tuple[str, str], Section] = field(default_factory=dict, repr=False)
    _inventory_cache: list[Inventory | None] = field(default_factory=list, repr=False)

    def hosts(self) -> Inventory | None:
        """The inventory of `[inventory] location`, read once; None without an inventory."""
        if not self._inventory_cache:
            from .inventory import inventory_format

            location, name = self.inventory.location, self.inventory.format
            if location:
                cls = inventory_format(name)
                self._inventory_cache.append(cls.load(location, self.plugin("inventories", name, cls.settings_class)))
            else:
                self._inventory_cache.append(None)
        return self._inventory_cache[0]

    def profile(self, name: str | None) -> Profile | None:
        if name is None:
            return None
        if name not in self.profiles:
            names = ", ".join(sorted(self.profiles)) or "none"
            raise SettingsError(f"no profile {name!r} in the settings (profiles: {names})")
        return self.profiles[name]

    def source(self, cls: type[Source]) -> SourceSettings:
        """The typed settings of one source, from its `[sources.NAME]` table."""
        return self.plugin("sources", cls.source, cls.settings_class)

    def plugin[S: Section](self, table: str, name: str, model: type[S]) -> S:
        """The typed settings of one plugin, from its `[TABLE.NAME]` table, read once."""
        if (table, name) not in self._plugin_cache:
            self._plugin_cache[(table, name)] = _plugin_settings(self, table, name, model)
        return cast(S, self._plugin_cache[(table, name)])

    @property
    def base(self) -> Path:
        return self.file.parent if self.file else Path.cwd()


@dataclass(frozen=True)
class SettingRow:
    key: str
    value: str
    origin: Origin
    note: str | None = None


def user_settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "snapdelta" / FILE_NAME


def find_settings_file(explicit: Path | None) -> Path | None:
    """The one file to read: the option, the environment, the current directory, then the user file."""
    chosen = explicit or (Path(os.environ[SETTINGS_ENV]) if os.environ.get(SETTINGS_ENV) else None)
    if chosen is not None:
        # ~ first: a written ~ is not a path until it is expanded, and is_file says no to it.
        chosen = chosen.expanduser()
        if not chosen.is_file():
            raise SettingsError(f"settings file {chosen} does not exist")
        # Absolute, so that the paths of the file stay right when the current directory changes.
        return chosen.absolute()
    for candidate in (Path.cwd() / FILE_NAME, user_settings_path()):
        if candidate.is_file():
            return candidate
    return None


def load_settings(
    explicit: Path | None = None,
    store: str | None = None,
    installed: Mapping[str, type[Source]] | None = None,
) -> Settings:
    path = find_settings_file(explicit)
    raw = _read(path) if path else {}
    try:
        parsed = FileSettings.model_validate(raw)
    except ValidationError as error:
        raise settings_error(path, [], error) from error
    settings = Settings(file=path, **{name: getattr(parsed, name) for name in FileSettings.model_fields})
    for section in CORE_SECTIONS:
        model = getattr(settings, section)
        written = raw.get(section, {})
        paths = {
            key: _resolve_path(value, settings.base)
            for key, value in model
            if isinstance(value, Path) and key in written
        }
        if paths:
            setattr(settings, section, model.model_copy(update=paths))
    for section in CORE_SECTIONS:
        for key in type(getattr(settings, section)).model_fields:
            present = key in raw.get(section, {})
            settings.origins[f"{section}.{key}"] = Origin.FILE if present else Origin.DEFAULT
    if "location" in raw.get("inventory", {}):
        settings.inventory = settings.inventory.model_copy(
            update={"location": _resolve_inventory(settings.inventory, settings.base)}
        )
    _apply_store(settings, store)
    from .secrets import use_settings

    use_settings(settings)
    models = plugin_models(installed)
    _check_plugins(settings, models)
    _check_plain_secrets(settings, models)
    from .notify import notifier_settings

    # A wrong notifier table must fail at the start, not in silence after the first run.
    notifier_settings(settings)
    _check_runner(settings)
    return settings


def _check_runner(settings: Settings) -> None:
    from .plugins import discover
    from .runners import RUNNERS

    found = discover(RUNNERS).plugins
    if settings.snapshot.runner not in found:
        message = f"no runner {settings.snapshot.runner!r} is installed (installed: {', '.join(sorted(found))})"
        where = settings.file or "settings"
        raise SettingsError(f"{where}: snapshot.runner: {message}", {"snapshot.runner": message})


def plugin_tables() -> dict[str, str]:
    """The settings table of each extension point that has one, with the word for one of its plugins."""
    from .plugins import extension_points

    return {point.table: point.kind for point in extension_points() if point.table is not None}


def plugin_models(installed: Mapping[str, type[Source]] | None = None) -> dict[str, dict[str, type[Section]]]:
    """The settings model of each installed plugin, by table: `[sources.NAME]`, `[stores.SCHEME]`, and so on.

    `installed` replaces the installed sources, for a caller that already holds them.
    """
    from .plugins import extension_points, plugins
    from .source import SOURCES

    models: dict[str, dict[str, type[Section]]] = {}
    for point in extension_points():
        if point.table is None:
            continue
        found = installed if point is SOURCES and installed is not None else plugins(point)
        models[point.table] = {name: value.settings_class for name, value in found.items()}
    return models


def setting_rows(settings: Settings, installed: Mapping[str, type[Source]] | None = None) -> list[SettingRow]:
    """Every value in use, with its origin; secrets masked."""
    rows = []
    for section in CORE_SECTIONS:
        model = getattr(settings, section)
        for key in type(model).model_fields:
            dotted = f"{section}.{key}"
            rows.append(
                SettingRow(
                    dotted, format_setting(getattr(model, key)), settings.origins[dotted], settings.notes.get(dotted)
                )
            )
    for table, models in plugin_models(installed).items():
        for name, model in sorted(models.items()):
            typed = settings.plugin(table, name, model)
            written = getattr(settings, table).get(name, {})
            for key in model.model_fields:
                origin = Origin.FILE if key in written else Origin.DEFAULT
                rows.append(SettingRow(f"{table}.{name}.{key}", format_setting(getattr(typed, key)), origin))
    from .notify import notifier_settings

    for name, (_, chosen) in sorted(notifier_settings(settings).items()):
        for key in type(chosen).model_fields:
            origin = Origin.FILE if key in chosen.model_fields_set else Origin.DEFAULT
            rows.append(SettingRow(f"notifiers.{name}.{key}", format_setting(getattr(chosen, key)), origin))
    for name, profile in sorted(settings.profiles.items()):
        for key in profile.model_fields_set:
            rows.append(SettingRow(f"profiles.{name}.{key}", format_setting(getattr(profile, key)), Origin.FILE))
    return rows


def format_setting(value: Any) -> str:
    if value is None:
        return "not set"
    if isinstance(value, Secret):
        return value.describe()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def read_settings_file(path: Path | None) -> dict[str, Any]:
    """The tables of a settings file as written, before validation; empty when there is no file."""
    return _read(path) if path is not None and path.is_file() else {}


def _read(path: Path) -> dict[str, Any]:
    # utf-8-sig drops a byte order mark that some editors write; tomllib refuses it.
    text = path.read_bytes().decode("utf-8-sig")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"{path}: {error}") from error


def _apply_store(settings: Settings, option: str | None) -> None:
    location = settings.store.location
    if location is not None:
        location = _local_address(location, lambda address: _resolve_path(Path(address), settings.base))
    # An option or an environment path stays relative to the current directory, unlike a path of the file.
    if os.environ.get(STORE_ENV):
        location = _local_address(os.environ[STORE_ENV], _expand_user)
        settings.origins["store.location"], settings.notes["store.location"] = Origin.ENV, STORE_ENV
    if option:
        location = _local_address(option, _expand_user)
        settings.origins["store.location"], settings.notes["store.location"] = Origin.OPTION, "--store"
    from .store import default_store_location

    settings.store = settings.store.model_copy(update={"location": location or default_store_location()})


def _local_address(location: str, change: Callable[[str], str | Path]) -> str:
    """The location with its address changed, when its backend reads a local path; any other stays as written."""
    from .store import SCHEME_SEPARATOR, split_location, store_classes

    scheme, address = split_location(location)
    backend = store_classes().get(scheme)
    if backend is None or not backend.local_path:
        return location
    changed = str(change(address))
    return f"{scheme}{SCHEME_SEPARATOR}{changed}" if SCHEME_SEPARATOR in location else changed


def _expand_user(address: str) -> str:
    """`~` and `~user` of a local path; a user that does not exist is a mistake, not a literal directory."""
    try:
        return str(Path(address).expanduser())
    except RuntimeError as error:
        raise SettingsError(f"cannot expand {address}: no such user") from error


def _resolve_inventory(inventory: InventorySettings, base: Path) -> str | None:
    """A relative location of a format that reads a local path is relative to the settings file."""
    from .inventory import inventory_formats

    found = inventory_formats().get(inventory.format)
    if inventory.location is None or found is None or not found.local_path:
        return inventory.location
    return str(_resolve_path(Path(inventory.location), base))


def _resolve_path(path: Path, base: Path) -> Path:
    path = Path(_expand_user(str(path)))
    return path if path.is_absolute() else base / path


def _plugin_settings[S: Section](settings: Settings, table: str, name: str, model: type[S]) -> S:
    written = getattr(settings, table).get(name, {})
    try:
        typed = model.model_validate(written)
    except ValidationError as error:
        raise settings_error(settings.file, [table, name], error) from error
    paths = {
        key: _resolve_path(value, settings.base) for key, value in typed if isinstance(value, Path) and key in written
    }
    return typed.model_copy(update=paths) if paths else typed


def _check_plugins(settings: Settings, models: Mapping[str, Mapping[str, type[Section]]]) -> None:
    kinds = plugin_tables()
    for table, installed in models.items():
        for name in getattr(settings, table):
            if name not in installed:
                names = ", ".join(sorted(installed)) or "none"
                settings.warnings.append(
                    f"{settings.file}: [{table}.{name}]: no {kinds[table]} {name!r} is installed (installed: {names})"
                )
        for name, model in installed.items():
            settings.plugin(table, name, model)


def _check_plain_secrets(settings: Settings, models: Mapping[str, Mapping[str, type[Section]]]) -> None:
    if settings.file is None or not settings.file.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH):
        return
    plain = (settings.web.token is not None and settings.web.token.is_plain) or any(
        isinstance(value, Secret) and value.is_plain
        for table, installed in models.items()
        for name, model in installed.items()
        if name in getattr(settings, table)
        for _, value in settings.plugin(table, name, model)
    )
    if plain:
        path = settings.file
        settings.warnings.append(f"{path} holds a plain secret and other users can read it: chmod 600 {path}")


def settings_error(path: Path | None, prefix: list[str], error: ValidationError) -> SettingsError:
    errors = {".".join([*prefix, *(str(part) for part in item["loc"])]): item["msg"] for item in error.errors()}
    where = path or "settings"
    return SettingsError("\n".join(f"{where}: {key}: {message}" for key, message in errors.items()), errors)


def save_settings(
    path: Path,
    values: dict[str, dict[str, Any]],
    installed: Mapping[str, type[Source]] | None = None,
    removed: Collection[str] = (),
) -> None:
    """Write the values that differ from their default and drop the `removed` tables (`profiles.NAME`).

    The rest of the file, comments included, stays.
    """
    models: dict[str, type[BaseModel]] = {section: type(getattr(Settings(), section)) for section in CORE_SECTIONS}
    for table, installed_models in plugin_models(installed).items():
        for name, model in installed_models.items():
            models[f"{table}.{name}"] = model
    checked: dict[str, BaseModel] = {}
    errors: dict[str, str] = {}
    for table in values:
        name = table.removeprefix("profiles.")
        if table.startswith("profiles.") and not PROFILE_NAME_RE.match(name):
            errors[table] = f"{name!r}: a profile name holds letters, digits, _ and - only"
        elif table.startswith("profiles."):
            models[table] = Profile
    for table, table_values in values.items():
        if table not in models:
            continue
        model = models[table]
        try:
            checked[table] = model.model_validate(
                {key: value for key, value in table_values.items() if value is not None}
            )
        except ValidationError as error:
            errors |= settings_error(path, table.split("."), error).errors
    if errors:
        raise SettingsError("\n".join(f"{path}: {key}: {message}" for key, message in errors.items()), errors)
    document = tomlkit.parse(path.read_text()) if path.is_file() else tomlkit.document()
    for table in removed:
        _remove_table(document, table.split("."))
    created: list[tuple[Any, str]] = []
    for table, model in checked.items():
        defaults = type(model)()
        target = _table(document, table.split("."), created)
        for key in type(model).model_fields:
            value = getattr(model, key)
            written = _toml_value(value)
            if value == getattr(defaults, key):
                target.pop(key, None)
            elif key not in target or target[key] != written:
                # An equal value stays as written, so 10 does not become 10.0.
                target[key] = written
    # Only the tables that this save added go away when empty: a table of the file can hold comments.
    # A new profile stays even when empty: its name is what the user asked for.
    for container, key in reversed(created):
        if not container[key] and container is not document.get("profiles"):
            del container[key]
    _write_atomic(path, tomlkit.dumps(document))


def _remove_table(document: Any, parts: list[str]) -> None:
    nodes: list[Any] = [document]
    for part in parts:
        node = nodes[-1].get(part)
        if node is None:
            return
        nodes.append(node)
    # tomlkit stores the comment above a table in the table before it: move the one of the next table there.
    if all(isinstance(node, Table) for node in nodes[1:]):
        container, start, stop = _comment_run_above(document, parts)
        kept = _tail(_last_container(nodes[-1]))
        blank_before = not start or isinstance(container.body[start - 1][1], Whitespace)
        if kept and isinstance(kept[0], Whitespace) and blank_before:
            kept = kept[1:]
        _replace_run(container, start, stop, kept)
    nodes[-2].pop(parts[-1])


def _node_container(node: Any) -> Container:
    return node.value if isinstance(node, Table) else node


def _last_container(table: Table) -> Container:
    """The container that ends the text of `table`, where a comment of the next table is stored."""
    container = table.value
    while True:
        last = next((item for key, item in reversed(container.body) if key is not None), None)
        if not isinstance(last, Table):
            return container
        container = last.value


def _tail(container: Container) -> list[Item]:
    start = next((i + 1 for i, (key, _) in reversed(list(enumerate(container.body))) if key is not None), 0)
    return [item for _, item in container.body[start:] if not isinstance(item, Null)]


def _comment_start(body: list[tuple[Key | None, Item]], stop: int) -> int:
    start = stop
    for index in range(stop - 1, -1, -1):
        key, item = body[index]
        if key is not None or isinstance(item, Whitespace):
            break
        if isinstance(item, Comment):
            start = index
    return start


def _comment_run_above(document: Any, parts: list[str]) -> tuple[Container, int, int]:
    """The container and the range of the comment lines right above the table `parts`."""
    parent = document
    for part in parts[:-1]:
        parent = parent[part]
    container = _node_container(parent)
    body = container.body
    stop = next(index for index, (key, _) in enumerate(body) if key is not None and key.key == parts[-1])
    begin = stop
    while begin and body[begin - 1][0] is None:
        begin -= 1
    previous = body[begin - 1][1] if begin else None
    if isinstance(previous, Table):
        tail = _last_container(previous)
        return tail, _comment_start(tail.body, len(tail.body)), len(tail.body)
    start = _comment_start(body, stop)
    if begin == 0 and start == stop and isinstance(parent, Table) and parent.is_super_table():
        return _comment_run_above(document, parts[:-1])
    return container, start, stop


def _replace_run(container: Container, start: int, stop: int, items: list[Item]) -> None:
    """Replace `container.body[start:stop]` by `items` and move the index of the keys after it."""
    shift = len(items) - (stop - start)
    container.body[start:stop] = [(None, item) for item in items]
    for key, index in container._map.items():
        container._map[key] = (
            tuple(i + shift if i >= stop else i for i in index)
            if isinstance(index, tuple)
            else (index + shift if index >= stop else index)
        )


def _table(document: Any, parts: list[str], created: list[tuple[Any, str]]) -> Any:
    current = document
    super_tables = (*plugin_tables(), "profiles")
    for part in parts:
        if part not in current:
            current[part] = tomlkit.table(is_super_table=part in super_tables)
            created.append((current, part))
        current = current[part]
    return current


def _toml_value(value: Any) -> Any:
    if isinstance(value, Secret):
        found = value.to_toml()
        if isinstance(found, str):
            return found
        table = tomlkit.inline_table()
        table.update(found)
        return table
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        table = tomlkit.inline_table()
        table.update({key: _toml_value(item) for key, item in value.items()})
        return table
    return value


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(text)
    temporary = Path(handle.name)
    temporary.chmod(mode)
    temporary.replace(path)
