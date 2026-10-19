"""Fetch several targets at the same time and save a snapshot of each; the inventory can choose the source of each."""

import contextlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .activity import collecting, recorded
from .errors import LoadError, SnapdeltaError
from .expected import ExpectedChange, check_expected
from .inventory import Host
from .output import Output, compare, described
from .plugins import require
from .runners import RUNNERS, LoadTask, OnProgress
from .settings import Settings, SourceSettings
from .source import Source, require_source, source_classes
from .store import Store
from .ticket import normalize_ticket


@dataclass(frozen=True)
class Taken:
    """A saved snapshot of take_snapshots(), with what its load filled and found."""

    reference: str
    loaded: list[str]
    warnings: list[str]


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
    *,
    ticket: str | None = None,
) -> tuple[dict[str, Taken], dict[str, str]]:
    """The saved snapshots and the error messages, by target; a failed target does not stop the others.

    The runner of `[snapshot] runner` runs the loads. The store records the command as a run of `origin` (cli,
    web, api, mcp), with the log of each load. `progress` hears each target when it starts and when it ends.
    """
    for cls in set(plan.values()):
        if require_source(cls.source) is not cls:
            # A task names its source, so the runner would load with the installed class of that name.
            raise SnapdeltaError(f"{cls.__qualname__} is not the source {cls.source!r} of the registry: register it")
    chosen = settings or Settings()
    name = chosen.snapshot.runner
    runner = require(RUNNERS, name)
    options = chosen.plugin("runners", name, runner.settings_class)
    tasks = [
        LoadTask(source=cls.source, target=target, label=label, ticket=ticket, captured_at=captured_at)
        for target, cls in plan.items()
    ]
    with recorded(store, "snapshot", origin, list(plan), chosen) as run:
        outcomes = runner.run(tasks, chosen, store, jobs, options, progress)
        for outcome in outcomes.values():
            run.log.extend(outcome.log)
        taken = {
            target: Taken(outcome.reference, outcome.loaded, outcome.warnings)
            for target, outcome in outcomes.items()
            if outcome.reference is not None
        }
        failures = {
            target: outcome.error or "failed" for target, outcome in outcomes.items() if outcome.reference is None
        }
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
    *,
    ticket: str | None = None,
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
    return take_snapshots(store, plan, label, captured_at, chosen, settings, origin=origin, ticket=ticket)


def load_source(
    cls: type[Source],
    files: Sequence[Path] = (),
    target: str | None = None,
    label: str | None = None,
    captured_at: datetime | None = None,
    settings: SourceSettings | None = None,
    host: Host | None = None,
    ticket: str | None = None,
) -> Source:
    """A loaded source; any error of the plugin becomes a LoadError, which the CLI and the web show as a message."""
    adapter = cls(
        files=files, target=target, label=label, ticket=ticket, captured_at=captured_at, settings=settings, host=host
    )
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
    ticket: str | None = None,
) -> Source:
    """A fresh fetch of the target of `left` (or of `target`) with the source of `left`, saved as a snapshot.

    The snapshot has `ticket`, else the ticket of `left`.
    """
    cls = require_source(left.source)
    if not cls.fetches:
        raise SnapdeltaError(
            f"source {left.source!r} reads input files, so it cannot fetch: take a snapshot of the new files, "
            "then compare the two snapshots"
        )
    name = target or left.target
    inventory = settings.hosts()
    host = inventory.host(name) if inventory and name else None
    loaded = load_source(
        cls,
        target=name,
        label=label,
        settings=settings.source(cls),
        host=host,
        ticket=normalize_ticket(ticket) or left.ticket,
    )
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
    ticket: str | None = None,
    note: str | None = None,
    expected: Sequence[ExpectedChange] | None = None,
    save: bool = True,
    origin: str = "python",
) -> Compared:
    """Compare the snapshot `left` with `right` (a snapshot or a loaded source) or with its target now (`live`).

    With `ticket`, the labels of `left` and `right` are looked up in the snapshots of that ticket, and the live
    snapshot has it. With `expected`, the output also holds its check against these expected changes. With `save`,
    the output and the live snapshot are saved, and the store records the command as a run.
    """
    if (right is None) == (not live):
        raise SnapdeltaError("give right, or live, not both")
    left_adapter = store.load_snapshot(store.resolve_snapshot(left, ticket))
    _check_expected_targets(expected, [target or left_adapter.target or left])
    run = recorded(store, "diff", origin, [left_adapter.target or left], settings) if save else contextlib.nullcontext()
    with run as record:
        live_reference = None
        if isinstance(right, Source):
            right_adapter = right
        elif right is not None:
            right_adapter = store.load_snapshot(store.resolve_snapshot(right, ticket))
        else:
            right_adapter = take_live(store, left_adapter, settings, target, live_label, save, ticket)
            live_reference = getattr(right_adapter, "reference", None)
        output = compare(left_adapter, right_adapter, models or None, settings.profile(profile), profile)
        if ticket or note:
            output = described(output, ticket=ticket, note=note)
        if expected is not None:
            output = output.model_copy(update={"expectations": check_expected(output, expected)})
        name = store.save_output(output) if save else None
        if record is not None:
            record.targets = list(dict.fromkeys([output.left.target, output.right.target]))
            record.saved = [live_reference] if live_reference else []
            record.outputs = [name] if name else []
            record.has_differences = output.has_differences()
    return Compared(output, name, live_reference)


@dataclass(frozen=True)
class TargetDiff:
    """The diff of one target of diff_targets(), or the reason why it has none."""

    target: str
    output: Output | None = None
    # The name of the saved output, and the reference of the snapshot that a live diff took.
    name: str | None = None
    live: str | None = None
    error: str | None = None

    @property
    def changed(self) -> bool:
        return self.output is not None and self.output.has_differences()

    @property
    def as_expected(self) -> bool | None:
        """Whether the diff holds the expected changes and no other; None without expected changes."""
        if self.output is None or self.output.expectations is None:
            return None
        return self.output.expectations.as_expected


def diff_targets(
    store: Store,
    settings: Settings,
    left: str,
    right: str | None = None,
    *,
    targets: Iterable[str] = (),
    groups: Iterable[str] = (),
    all_targets: bool = False,
    live: bool = False,
    live_label: str = "live",
    models: Sequence[str] | None = None,
    profile: str | None = None,
    ticket: str | None = None,
    note: str | None = None,
    expected: Sequence[ExpectedChange] | None = None,
    save: bool = True,
    origin: str = "python",
    jobs: int | None = None,
) -> dict[str, TargetDiff]:
    """For each target, compare TARGET@LEFT with TARGET@RIGHT, or with the target now (`live`); by target.

    `left` and `right` are labels, or `latest`. A target that fails does not stop the others. With `live`, the
    runner takes the snapshots of every target at the same time, in one snapshot run; the compares are one diff run.
    """
    left, right = left.removeprefix("@"), right.removeprefix("@") if right is not None else None
    if (right is None) == (not live):
        raise SnapdeltaError("give the label of the right side, or live, not both")
    if live and not save:
        raise SnapdeltaError("a live diff of many targets saves the snapshots that it takes")
    chosen_profile = settings.profile(profile)
    names = _diff_names(store, settings, left, targets, groups, all_targets, ticket)
    _check_expected_targets(expected, names)
    results: dict[str, TargetDiff] = {}
    lefts: dict[str, str] = {}
    for name in names:
        try:
            lefts[name] = store.resolve_snapshot(f"{name}@{left}", ticket)
        except SnapdeltaError as error:
            results[name] = TargetDiff(name, error=str(error))
    rights = _live_rights(store, settings, lefts, live_label, jobs, origin, results, ticket) if live else {}
    if not live:
        for name in lefts:
            try:
                rights[name] = store.resolve_snapshot(f"{name}@{right}", ticket)
            except SnapdeltaError as error:
                results[name] = TargetDiff(name, error=str(error))
    run = recorded(store, "diff", origin, names, settings) if save else contextlib.nullcontext()
    with run as record:
        for name in names:
            if name not in rights:
                continue
            try:
                output = compare(
                    store.load_snapshot(lefts[name]), store.load_snapshot(rights[name]), models, chosen_profile, profile
                )
                if ticket or note:
                    output = described(output, ticket=ticket, note=note)
                if expected is not None:
                    output = output.model_copy(update={"expectations": check_expected(output, expected)})
                saved = store.save_output(output) if save else None
            except Exception as error:
                # A target that fails, even on a bug of a plugin, must not stop the diffs of the others.
                results[name] = TargetDiff(name, error=_message(name, error))
                continue
            results[name] = TargetDiff(name, output, saved, rights[name] if live else None)
        if record is not None:
            done = [results[name] for name in names]
            record.outputs = [item.name for item in done if item.name]
            record.saved = [item.live for item in done if item.live]
            record.errors = {item.target: item.error for item in done if item.error}
            record.has_differences = any(item.changed for item in done)
    return {name: results[name] for name in names}


def _diff_names(
    store: Store,
    settings: Settings,
    left: str,
    targets: Iterable[str],
    groups: Iterable[str],
    all_targets: bool,
    ticket: str | None,
) -> list[str]:
    """The targets of diff_targets(): the names, the hosts of `groups`, and with `all_targets` each target with LEFT."""
    names = list(dict.fromkeys(name.strip() for name in targets if name.strip()))
    wanted_groups = list(groups)
    if wanted_groups:
        inventory = settings.hosts()
        if inventory is None:
            raise SnapdeltaError("a group needs an inventory: set [inventory] location in the settings")
        names = inventory.select(names, wanted_groups)
    if all_targets:
        with_left = [name for name in store.targets() if _has(store, f"{name}@{left}", ticket)]
        names = list(dict.fromkeys([*names, *with_left]))
    if not names:
        raise SnapdeltaError("give targets, groups or all_targets")
    return names


def _has(store: Store, reference: str, ticket: str | None) -> bool:
    try:
        store.resolve_snapshot(reference, ticket)
    except SnapdeltaError:
        return False
    return True


def _live_rights(
    store: Store,
    settings: Settings,
    lefts: Mapping[str, str],
    label: str,
    jobs: int | None,
    origin: str,
    results: dict[str, TargetDiff],
    ticket: str | None,
) -> dict[str, str]:
    """Take a snapshot of each target now, with the source of its left snapshot; the references by target."""
    plan: dict[str, type[Source]] = {}
    for name, reference in lefts.items():
        try:
            cls = require_source(store.read_snapshot(reference).source)
        except SnapdeltaError as error:
            results[name] = TargetDiff(name, error=str(error))
            continue
        if cls.fetches:
            plan[name] = cls
        else:
            results[name] = TargetDiff(
                name, error=f"{name}: the source {cls.source!r} reads input files, so it cannot fetch"
            )
    if not plan:
        return {}
    taken, failures = take_snapshots(
        store, plan, label, jobs=jobs or settings.snapshot.jobs, settings=settings, origin=origin, ticket=ticket
    )
    for name, message in failures.items():
        results[name] = TargetDiff(name, error=message)
    return {name: item.reference for name, item in taken.items()}


def _message(target: str, error: Exception) -> str:
    return str(error) if isinstance(error, SnapdeltaError) else f"{target}: {type(error).__name__}: {error}"


def _check_expected_targets(expected: Sequence[ExpectedChange] | None, names: Sequence[str]) -> None:
    """An expected change of a target that the diff does not compare would never count as missing."""
    unknown = sorted({item.target for item in expected or [] if item.target is not None and item.target not in names})
    if unknown:
        raise SnapdeltaError(
            f"expected changes name targets that this diff does not compare: {', '.join(unknown)} "
            f"(compared: {', '.join(names)})"
        )
