"""The models of the installed sources, read from their classes: what a profile can name and which sources compare."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, get_args

from diffsync import DiffSyncModel

from .plugins import package_of
from .source import SOURCES, Source, model_groups

# diffsync joins the identifiers of an object with this into its unique id.
ID_SEPARATOR = "__"
MODULE_PATH_RE = re.compile(r"\b(?:\w+\.)+(\w+)")


@dataclass(frozen=True)
class ModelField:
    name: str
    type: str


@dataclass(frozen=True)
class ModelInfo:
    name: str
    doc: str | None
    identifiers: list[ModelField]
    attributes: list[ModelField]
    children: list[str]
    parent: str | None
    # Kept in the snapshot, never compared.
    context: list[ModelField] = field(default_factory=list)
    # The inventory groups of the hosts that load this top-level model; empty: every host.
    groups: list[str] = field(default_factory=list)

    @property
    def unique_id(self) -> str:
        """The form of the unique id of an object, for example `interface__index`."""
        return ID_SEPARATOR.join(item.name for item in self.identifiers)

    @property
    def shape(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """What two sources must share for this model to compare: the names of the identifiers and attributes."""
        return tuple(item.name for item in self.identifiers), tuple(item.name for item in self.attributes)


@dataclass(frozen=True)
class SourceModels:
    source: str
    distribution: str | None
    fetches: bool
    doc: str | None
    models: list[ModelInfo]
    top_level: list[str]


@dataclass
class ModelSet:
    """Sources with the same models: one table of models serves them all."""

    sources: list[SourceModels]
    # The sources of other sets that share top-level models with this one, so that a compare between them works.
    compares_with: list[str] = field(default_factory=list)
    # One message per model that another set names the same way with other fields.
    conflicts: list[str] = field(default_factory=list)

    @property
    def models(self) -> list[ModelInfo]:
        return self.sources[0].models

    @property
    def names(self) -> list[str]:
        return [item.source for item in self.sources]

    @property
    def top_level(self) -> list[str]:
        return self.sources[0].top_level


def describe(cls: type[Source], source: str | None = None) -> SourceModels:
    """The models of one source class, top-level models first, each followed by its children."""
    order = cls.model_order()
    parents = {child: name for name in order for child in _model(cls, name).get_children_mapping()}
    models = [_model_info(name, _model(cls, name), parents.get(name)) for name in order]
    return SourceModels(
        source=source or cls.source,
        distribution=package_of(SOURCES, source or cls.source),
        fetches=cls.fetches,
        doc=inspect.getdoc(cls),
        models=models,
        top_level=list(cls.top_level),
    )


def catalog(classes: Mapping[str, type[Source]]) -> list[ModelSet]:
    """The installed sources grouped by the same models, with the other sets that they compare with or conflict with."""
    groups: dict[tuple[Any, ...], list[SourceModels]] = {}
    for name, cls in sorted(classes.items()):
        item = describe(cls, name)
        key = tuple((model.name, model.shape, tuple(model.children)) for model in item.models)
        groups.setdefault(key, []).append(item)
    sets = [ModelSet(sources) for sources in groups.values()]
    for found in sets:
        own = {model.name: model for model in found.models}
        for other in sets:
            if other is found:
                continue
            if any(name in other.top_level for name in found.top_level):
                found.compares_with += other.names
            others = {model.name: model for model in other.models}
            found.conflicts += [
                _conflict(own[name], others[name], ", ".join(other.names))
                for name in own
                if name in others and own[name].shape != others[name].shape
            ]
    return sets


def _model(cls: type[Source], name: str) -> type[DiffSyncModel]:
    return getattr(cls, name)


def context_fields(model: type[DiffSyncModel]) -> list[str]:
    """The fields that a snapshot keeps but that diffsync never compares: not an identifier, attribute or child."""
    compared = {*model._identifiers, *model._attributes, *model._children.values(), *DiffSyncModel.model_fields}
    return [name for name in model.model_fields if name not in compared]


def _model_info(name: str, model: type[DiffSyncModel], parent: str | None) -> ModelInfo:
    fields = model.model_fields
    return ModelInfo(
        name=name,
        doc=inspect.getdoc(model) if model.__doc__ else None,
        identifiers=[ModelField(key, _type_name(fields[key].annotation)) for key in model._identifiers],
        attributes=[ModelField(key, _type_name(fields[key].annotation)) for key in model._attributes],
        children=list(model.get_children_mapping()),
        parent=parent,
        context=[ModelField(key, _type_name(fields[key].annotation)) for key in context_fields(model)],
        groups=list(model_groups(model)) if parent is None else [],
    )


def _type_name(annotation: Any) -> str:
    """`int`, `str | None`, `list[str]`, `InterfaceType | None`: the type as a reader writes it, without modules."""
    if isinstance(annotation, type) and not get_args(annotation):
        return annotation.__name__
    return MODULE_PATH_RE.sub(r"\1", str(annotation).replace("typing.", ""))


def _conflict(own: ModelInfo, other: ModelInfo, source: str) -> str:
    own_fields = set(own.shape[0]) | set(own.shape[1])
    other_fields = set(other.shape[0]) | set(other.shape[1])
    parts = []
    if own.shape[0] != other.shape[0]:
        parts.append(f"identifiers {', '.join(own.shape[0])} here, {', '.join(other.shape[0])} in {source}")
    if only_here := sorted(own_fields - other_fields):
        parts.append(f"only here: {', '.join(only_here)}")
    if only_there := sorted(other_fields - own_fields):
        parts.append(f"only in {source}: {', '.join(only_there)}")
    return f"{own.name}: not the same fields as in {source} ({'; '.join(parts) or 'other order'})"
