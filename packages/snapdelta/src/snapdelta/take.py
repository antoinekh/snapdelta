"""Fetch several targets at the same time and save a snapshot of each; the inventory can choose the source of each."""

import contextlib
import functools
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .activity import collecting, recorded
from .errors import LoadError, SnapdeltaError
from .inventory import Host
from .output import Output, compare, described
from .parallel import run_parallel
from .settings import Settings, SourceSettings
from .source import Source, require_source, source_classes
from .store import Store


@dataclass(frozen=True)
class Taken:
    loaded: Source
    reference: str


@dataclass(frozen=True)
class Progress:
    """Where one target of take_snapshots() stands: running, then saved or failed."""

    status: str
    reference: str | None = None
    error: str | None = None


type OnProgress = Callable[[str, Progress], None]


def target_names(
    cls: type[Source] | None,
    targets: Iterable[str],
    all_targets: bool,
    settings: Settings,
    groups: Iterable[str] = (),
) -> list[str]:
    """The targets to fetch, without duplicates: the names, the hosts of `groups`, and with `all_targets` every target.

    Every target is the list of the source when one is given, else every host of the inventory.
    """
    names = list(dict.fromkeys(name.strip() for name in targets if name.strip()))
    inventory = settings.hosts()
    wanted_groups = list(groups)
    if wanted_groups:
        if inventory is None:
            raise SnapdeltaError("a group needs an inventory: set [inventory] location in the settings")
        names = inventory.select(names, wanted_groups)
    if not all_targets:
        return names
    if cls is None:
        if inventory is None:
            raise SnapdeltaError("give --source, or set an inventory to take every host")
        return sorted(set(inventory.hosts) | set(names))
    if not cls.fetches:
        raise SnapdeltaError(f"source {cls.source!r} reads input files, so it lists no targets")
    found = cls.targets(settings.source(cls))
    if not found:
        raise SnapdeltaError(f"source {cls.source!r} lists no target: give the target names")
    return sorted(set(found) | set(names))


def platform_source(settings: Settings, platform: str | None) -> str | None:
    """The source of a platform: its line in `[inventory.platforms]`, else the installed source of the same name."""
    if not platform:
        return None
    if platform in settings.inventory.platforms:
        return settings.inventory.platforms[platform]
    return platform if platform in source_classes() else None


def plan_sources(names: Iterable[str], settings: Settings, cls: type[Source] | None = None) -> dict[str, type[Source]]:
    """The source of each target: `cls` for all, else the source of the platform of its host in the inventory."""
    if cls is not None:
        return dict.fromkeys(names, cls)
    inventory = settings.hosts()
    if inventory is None:
        raise SnapdeltaError("give --source: with no inventory, the platform of a target is not known")
    plan: dict[str, type[Source]] = {}
    problems = []
    for name in names:
        host = inventory.host(name)
        source = platform_source(settings, host.platform) if host else None
        if host is None:
            problems.append(f"{name} is not in the inventory")
        elif source is None:
            problems.append(
                f"{name}: no source for the platform {host.platform!r}: install a source of that name, "
                "or map it in [inventory.platforms]"
            )
        elif (found := require_source(source)).fetches:
            plan[name] = found
        else:
            problems.append(f"{name}: the source {source!r} of the platform {host.platform!r} reads input files")
    if problems:
        raise SnapdeltaError("; ".join(problems))
    return plan


def take_snapshots(
    store: Store,
    plan: Mapping[str, type[Source]],
    label: str | None = None,
    captured_at: datetime | None = None,
    jobs: int = 4,
    settings: Settings | None = None,
    origin: str = "python",
    progress: OnProgress | None = None,
) -> tuple[dict[str, Taken], dict[str, str]]:
    """The saved snapshots and the error messages by target; a failed target does not stop the others.

    The store records the command as a run of `origin` (cli, web, api, mcp), with the log of each load.
    `progress` hears each target when it starts and when it ends, from the thread of its load.
    """
    chosen = settings or Settings()
    inventory = chosen.hosts()
    tasks = {
        target: functools.partial(
            _take,
            store,
            cls,
            target,
            label,
            captured_at,
            chosen.source(cls),
            inventory.host(target) if inventory else None,
        )
        for target, cls in plan.items()
    }
    if progress is not None:
        tasks = {target: functools.partial(_reported, target, task, progress) for target, task in tasks.items()}
    with recorded(store, "snapshot", origin, list(plan)) as run:
        taken, errors = run_parallel(tasks, jobs)
        failures = {target: _message(target, error) for target, error in errors.items()}
        run.saved = [item.reference for item in taken.values()]
        run.errors = failures
    return taken, failures


def snapshot_targets(
    store: Store,
    settings: Settings,
    source: str | None = None,
    targets: Iterable[str] = (),
    groups: Iterable[str] = (),
    all_targets: bool = False,
    label: str | None = None,
    origin: str = "python",
    jobs: int | None = None,
    captured_at: datetime | None = None,
) -> tuple[dict[str, Taken], dict[str, str]]:
    """Fetch the named targets, the hosts of `groups`, or every target, with `source` or the source of each platform.

    `jobs` targets load at the same time; default: `[snapshot] jobs`.
    """
    cls = require_source(source) if source else None
    names = target_names(cls, targets, all_targets, settings, groups)
    if not names:
        raise SnapdeltaError("give targets, groups or all_targets")
    plan = plan_sources(names, settings, cls)
    chosen = jobs or settings.snapshot.jobs
    return take_snapshots(store, plan, label, captured_at, chosen, settings, origin=origin)


def load_source(
    cls: type[Source],
    files: Sequence[Path] = (),
    target: str | None = None,
    label: str | None = None,
    captured_at: datetime | None = None,
    settings: SourceSettings | None = None,
    host: Host | None = None,
) -> Source:
    """A loaded source; any error of the plugin becomes a LoadError, which the CLI and the web show as a message."""
    adapter = cls(files=files, target=target, label=label, captured_at=captured_at, settings=settings, host=host)
    with collecting(target) as lines:
        try:
            adapter.load()
        except SnapdeltaError:
            raise
        except Exception as error:
            # A plugin bug is not a SnapdeltaError: its type helps more than its text alone.
            raise LoadError(target or cls.source, f"{type(error).__name__}: {error}") from error
    adapter.log = lines
    return adapter


def take_live(
    store: Store,
    left: Source,
    settings: Settings,
    target: str | None = None,
    label: str | None = "live",
    save: bool = True,
) -> Source:
    """A fresh fetch of the target of `left` (or of `target`) with the source of `left`, saved as a snapshot."""
    cls = require_source(left.source)
    if not cls.fetches:
        raise SnapdeltaError(f"source {left.source!r} reads input files, so it cannot fetch: give --source and --file")
    name = target or left.target
    inventory = settings.hosts()
    host = inventory.host(name) if inventory and name else None
    loaded = load_source(cls, target=name, label=label, settings=settings.source(cls), host=host)
    if not save:
        return loaded
    # The saved snapshot gives the output a reference, so that its page shows the unchanged values too.
    return store.load_snapshot(store.save_snapshot(loaded))


@dataclass(frozen=True)
class Compared:
    output: Output
    # The name of the saved output, and the reference of the snapshot that a live diff saved.
    name: str | None
    live: str | None = None


def diff_snapshots(
    store: Store,
    settings: Settings,
    left: str,
    right: str | Source | None = None,
    *,
    live: bool = False,
    target: str | None = None,
    live_label: str | None = "live",
    models: Sequence[str] | None = None,
    profile: str | None = None,
    title: str | None = None,
    description: str | None = None,
    save: bool = True,
    origin: str = "python",
) -> Compared:
    """Compare the snapshot `left` with `right` (a snapshot or a loaded source) or with its target now (`live`).

    With `save`, the output and the live snapshot are saved, and the store records the command as a run.
    """
    if (right is None) == (not live):
        raise SnapdeltaError("give right, or live, not both")
    left_adapter = store.load_snapshot(store.resolve_snapshot(left))
    run = recorded(store, "diff", origin, [left_adapter.target or left]) if save else contextlib.nullcontext()
    with run as record:
        live_reference = None
        if isinstance(right, Source):
            right_adapter = right
        elif right is not None:
            right_adapter = store.load_snapshot(store.resolve_snapshot(right))
        else:
            right_adapter = take_live(store, left_adapter, settings, target, live_label, save)
            live_reference = getattr(right_adapter, "reference", None)
        output = compare(left_adapter, right_adapter, models or None, settings.profile(profile), profile)
        if title or description:
            output = described(output, title, description)
        name = store.save_output(output) if save else None
        if record is not None:
            record.targets = list(dict.fromkeys([output.left.target, output.right.target]))
            record.saved = [live_reference] if live_reference else []
            record.output = name
    return Compared(output, name, live_reference)


def _take(
    store: Store,
    cls: type[Source],
    target: str,
    label: str | None,
    captured_at: datetime | None,
    settings: SourceSettings,
    host: Host | None,
) -> Taken:
    loaded = load_source(cls, target=target, label=label, captured_at=captured_at, settings=settings, host=host)
    return Taken(loaded, store.save_snapshot(loaded))


def _reported(target: str, task: Callable[[], Taken], progress: OnProgress) -> Taken:
    progress(target, Progress("running"))
    try:
        taken = task()
    except Exception as error:
        progress(target, Progress("failed", error=_message(target, error)))
        raise
    progress(target, Progress("saved", reference=taken.reference))
    return taken


def _message(target: str, error: Exception) -> str:
    if isinstance(error, SnapdeltaError):
        return str(error)
    return f"{target}: {type(error).__name__}: {error}"
