"""`snapdelta plugins new`: a plugin package to start from, with one working plugin per kind and its tests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from ..errors import SnapdeltaError

NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
TEMPLATES = files(__package__)


@dataclass(frozen=True)
class Kind:
    """One kind of plugin: its module, the entry point group, and the object that the entry point names."""

    module: str
    group: str
    attribute: str
    words: str
    # The packages that its plugin needs besides snapdelta, at the same version range.
    requires: tuple[str, ...] = ()


KINDS = {
    "source": Kind("source", "snapdelta.sources", "{Class}Source", "a source"),
    "store": Kind("store", "snapdelta.stores", "{Class}Store", "a store backend"),
    "format": Kind("render", "snapdelta.renderers", "{CONSTANT}", "an output format"),
    "inventory": Kind("inventory", "snapdelta.inventories", "{Class}Inventory", "an inventory format"),
    "secret": Kind("secrets", "snapdelta.secrets", "{Class}Secrets", "a secret backend"),
    "notifier": Kind("notify", "snapdelta.notifiers", "{Class}Notifier", "a notifier"),
    "rule": Kind("rules", "snapdelta.rules", "{Class}Rule", "a compare rule"),
    "runner": Kind("runner", "snapdelta.runners", "{Class}Runner", "a runner"),
    "auth": Kind("auth", "snapdelta.auth", "{Class}Auth", "an auth backend of the web UI", ("snapdelta-web",)),
}


@dataclass(frozen=True)
class Names:
    name: str
    distribution: str
    package: str
    cls: str
    constant: str

    @classmethod
    def of(cls, name: str) -> Names:
        if not NAME_RE.match(name):
            raise SnapdeltaError(f"{name!r}: a plugin name holds lower-case letters, digits and single dashes")
        words = name.split("-")
        return cls(
            name=name,
            distribution=f"snapdelta-{name}",
            package=f"snapdelta_{'_'.join(words)}",
            cls="".join(word.capitalize() for word in words),
            constant=f"{'_'.join(words).upper()}_FORMAT",
        )


def snapdelta_range() -> str:
    """The versions of snapdelta that a plugin of this one supports: a minor version may change the interfaces."""
    major, minor, *_ = version("snapdelta").split(".")
    return f">={major}.{minor},<{major}.{int(minor) + 1}"


def new_plugin(name: str, kinds: list[str], path: Path) -> list[Path]:
    """Write a plugin package at `path` with one plugin of each kind; the files written, in order."""
    unknown = [kind for kind in kinds if kind not in KINDS]
    if unknown or not kinds:
        raise SnapdeltaError(f"give kinds among {', '.join(KINDS)}; unknown: {', '.join(unknown) or 'none'}")
    names = Names.of(name)
    if path.exists() and any(path.iterdir()):
        raise SnapdeltaError(f"{path} is not empty: give another --path")
    chosen = [KINDS[kind] for kind in dict.fromkeys(kinds)]
    values = {
        "name": names.name,
        "distribution": names.distribution,
        "package": names.package,
        "Class": names.cls,
        "CONSTANT": names.constant,
        "snapdelta_range": snapdelta_range(),
        "dependencies": ", ".join(
            f'"{package}{snapdelta_range()}"'
            for package in dict.fromkeys(["snapdelta", *(name for kind in chosen for name in kind.requires)])
        ),
        "kinds": ", ".join(kind.words for kind in chosen),
        "entry_points": _entry_points(names, chosen),
    }
    plan = {
        Path("pyproject.toml"): "pyproject.toml.tmpl",
        Path("README.md"): "README.md.tmpl",
        Path("src", names.package, "__init__.py"): "__init__.py.tmpl",
    }
    for kind in chosen:
        plan[Path("src", names.package, f"{kind.module}.py")] = f"kinds/{kind.module}.py.tmpl"
        plan[Path("tests", f"test_{kind.module}.py")] = f"kinds/test_{kind.module}.py.tmpl"
    written = []
    for relative, template in plan.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_fill(TEMPLATES.joinpath(template).read_text(encoding="utf-8"), values), encoding="utf-8")
        written.append(target)
    return written


def _entry_points(names: Names, kinds: list[Kind]) -> str:
    tables = []
    for kind in kinds:
        attribute = kind.attribute.format(Class=names.cls, CONSTANT=names.constant)
        tables.append(
            f'[project.entry-points."{kind.group}"]\n{names.name} = "{names.package}.{kind.module}:{attribute}"\n'
        )
    return "\n".join(tables)


def _fill(template: str, values: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda match: values[match[1]], template)
