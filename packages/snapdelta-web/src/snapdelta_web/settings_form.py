"""The forms of the Admin tabs Settings and Profiles: one group per table, built from the settings models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from snapdelta.settings import (
    CORE_SECTIONS,
    PROFILE_NAME_RE,
    Origin,
    Profile,
    Secret,
    Settings,
    SettingsError,
    format_setting,
    plugin_models,
    plugin_tables,
    read_settings_file,
)
from snapdelta.source import Source
from snapdelta.store import default_store_path

Kind = Literal["text", "number", "bool", "secret", "list", "table"]
# The group of the form that adds a profile; its name comes from the field NEW_PROFILE_NAME.
NEW_PROFILE = "profiles.+"
NEW_PROFILE_NAME = "new_profile"


@dataclass(frozen=True)
class FormField:
    key: str
    table: str
    name: str
    kind: Kind
    value: str
    default: str
    origin: Origin
    in_use: str
    note: str | None
    secret: Secret | None = None
    # From the `description` and the `examples` of the field in its settings model.
    help: str | None = None
    example: str | None = None


@dataclass(frozen=True)
class FormGroup:
    table: str
    title: str
    fields: list[FormField]
    # A profile group can be removed; the other tables always exist.
    removable: bool = False


@dataclass(frozen=True)
class FormValues:
    """The posted values by table, and the tables to remove."""

    values: dict[str, dict[str, Any]]
    removed: list[str] = field(default_factory=list)


def form_groups(settings: Settings, installed: Mapping[str, type[Source]]) -> list[FormGroup]:
    """The core tables, then one table per installed plugin that declares settings; the profiles have their own tab."""
    raw = read_settings_file(settings.file)
    groups = [
        _group(section, section, getattr(settings, section), raw.get(section, {}), settings)
        for section in CORE_SECTIONS
    ]
    kinds = plugin_tables()
    for table, models in plugin_models(installed).items():
        for name, model in sorted(models.items()):
            if model.model_fields:
                written = raw.get(table, {}).get(name, {})
                title = f"{kinds[table].capitalize()} {name}"
                groups.append(_group(f"{table}.{name}", title, settings.plugin(table, name, model), written, settings))
    return groups


def profile_groups(settings: Settings) -> list[FormGroup]:
    """One group per profile, then the group that adds a profile."""
    written = read_settings_file(settings.file).get("profiles", {})
    groups = []
    for name, profile in sorted(settings.profiles.items()):
        group = _group(f"profiles.{name}", name, profile, written.get(name, {}), settings)
        groups.append(FormGroup(group.table, group.title, group.fields, removable=True))
    groups.append(_group(NEW_PROFILE, "New profile", Profile(), {}, settings))
    return groups


def parse_form(form: Mapping[str, str], groups: list[FormGroup]) -> FormValues:
    """The posted values by table; an empty field goes back to its default, an empty secret keeps the current one."""
    parsed = FormValues({})
    errors: dict[str, str] = {}
    for group in groups:
        if group.removable and form.get(f"{group.table}.remove"):
            parsed.removed.append(group.table)
            continue
        table: dict[str, Any] = {}
        for item in group.fields:
            if item.kind == "bool":
                table[item.name] = item.key in form
            elif item.kind == "secret":
                table[item.name] = _secret(form, item)
            elif item.kind == "list":
                table[item.name] = [
                    line.strip() for line in form.get(item.key, "").splitlines() if line.strip()
                ] or None
            elif item.kind == "table":
                table[item.name], error = _table(form.get(item.key, ""))
                if error:
                    errors[item.key] = error
            else:
                table[item.name] = form.get(item.key, "").strip() or None
        if group.table == NEW_PROFILE:
            name = form.get(NEW_PROFILE_NAME, "").strip()
            if not name:
                continue
            if not PROFILE_NAME_RE.match(name):
                errors[NEW_PROFILE_NAME] = f"{name!r}: a profile name holds letters, digits, _ and - only"
            elif any(group.table == f"profiles.{name}" for group in groups):
                errors[NEW_PROFILE_NAME] = f"a profile {name!r} exists already"
            parsed.values[f"profiles.{name}"] = table
        else:
            parsed.values[group.table] = table
    if errors:
        raise SettingsError("\n".join(f"{key}: {message}" for key, message in errors.items()), errors)
    return parsed


def errors_by_field(errors: Mapping[str, str], groups: list[FormGroup], new_name: str) -> dict[str, str]:
    """The errors under the key of their form field: `profiles.x.tolerance.interface.mtu` is the field `tolerance`."""
    keys = sorted((item.key for group in groups for item in group.fields), key=len, reverse=True)
    found: dict[str, str] = {}
    for key, message in errors.items():
        if new_name and key.startswith(f"profiles.{new_name}."):
            key = NEW_PROFILE + key.removeprefix(f"profiles.{new_name}")
        field_key = next((candidate for candidate in keys if key == candidate or key.startswith(f"{candidate}.")), key)
        found.setdefault(field_key, message)
    return found


def _table(text: str) -> tuple[dict[str, str] | None, str | None]:
    """`KEY = VALUE` lines as a table, or the error of the first line that is not one."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip() or not value.strip():
            return None, f"{line.strip()!r}: write one KEY = VALUE per line"
        found[key.strip()] = value.strip()
    return found or None, None


def _group(table: str, title: str, model: BaseModel, written: Mapping[str, Any], settings: Settings) -> FormGroup:
    defaults = type(model)()
    fields = []
    for name, info in type(model).model_fields.items():
        key = f"{table}.{name}"
        origin = settings.origins.get(key, Origin.FILE if name in written else Origin.DEFAULT)
        kind = _kind(info.annotation)
        value = getattr(model, name)
        secret = value if isinstance(value, Secret) else None
        shown = _shown(written.get(name), kind)
        fields.append(
            FormField(
                key,
                table,
                name,
                kind,
                shown,
                _default_text(key, getattr(defaults, name)),
                origin,
                format_setting(value),
                settings.notes.get(key),
                secret,
                info.description,
                str(info.examples[0]) if info.examples else None,
            )
        )
    return FormGroup(table, title, fields)


def _default_text(key: str, default: Any) -> str:
    # The model gives no store location: the default path depends on XDG_DATA_HOME when snapdelta runs.
    if key == "store.location":
        path = default_store_path()
        return f"~/{path.relative_to(Path.home())}" if path.is_relative_to(Path.home()) else str(path)
    return format_setting(default)


def _shown(written: Any, kind: Kind) -> str:
    if written is None or kind == "secret":
        return ""
    if isinstance(written, bool):
        return "true" if written else ""
    if kind == "list" and isinstance(written, list):
        return "\n".join(str(item) for item in written)
    if kind == "table" and isinstance(written, dict):
        return "\n".join(f"{key} = {value}" for key, value in written.items())
    return str(written)


def _kind(annotation: Any) -> Kind:
    union = get_origin(annotation) in (Union, UnionType)
    options = [item for item in get_args(annotation) if item is not NoneType] if union else [annotation]
    first = options[0] if options else str
    if get_origin(first) is list:
        return "list"
    if get_origin(first) is dict:
        return "table"
    if first is bool:
        return "bool"
    if first is Secret:
        return "secret"
    if first in (int, float):
        return "number"
    return "text"


def _secret(form: Mapping[str, str], item: FormField) -> Secret | None:
    env, plain = form.get(f"{item.key}.env", "").strip(), form.get(f"{item.key}.plain", "")
    if env:
        return Secret(env=env)
    if plain:
        return Secret(plain=plain)
    if form.get(f"{item.key}.clear"):
        return None
    return item.secret
