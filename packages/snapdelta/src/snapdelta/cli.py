"""Command line interface: load files into snapshots, compare them, show saved outputs."""

import functools
import getpass
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from diffsync.logging import enable_console_logging

from .about import version_line
from .activity import configure_logging
from .catalog import catalog
from .doctor import PluginReport, diagnose, plugin_reports
from .errors import SnapdeltaError
from .expected import read_expected
from .output import Output, new_review
from .plugins import Problem, Severity, package_of
from .render import Renderer, render_snapshot, renderer
from .scaffold import KINDS, new_plugin
from .settings import Settings, load_settings, setting_rows
from .source import SOURCES, Source, broken_sources, require_source, source_classes
from .store import Store, age_cutoff, open_store_from
from .take import TargetDiff, diff_snapshots, diff_targets, load_source, platform_source, snapshot_targets, target_names

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Snapshot anything with diffsync models, then compare two snapshots.",
)
delete_app = typer.Typer(no_args_is_help=True, help="Delete snapshots or outputs.")
app.add_typer(delete_app, name="delete")
plugins_app = typer.Typer(help="List the plugins of every extension point, or write a new plugin package.")
app.add_typer(plugins_app, name="plugins")

Files = Annotated[
    list[Path] | None,
    typer.Argument(exists=True, dir_okay=False, help="Input files of one target; none for a source that fetches."),
]
SourceName = Annotated[str, typer.Option(help="Source, see `snapdelta sources`.")]
Label = Annotated[str | None, typer.Option(help="Label of the snapshot, for example before-swap.")]
Json = Annotated[bool, typer.Option("--json", help="Print JSON instead of text.")]
# The options of the three commands snapdelta, snapdelta-web and snapdelta-mcp.
StoreOption = Annotated[
    str | None,
    typer.Option("--store", help="Store directory, or SCHEME://ADDRESS; wins over SNAPDELTA_STORE and the settings."),
]
SettingsOption = Annotated[
    Path | None,
    typer.Option(
        "--settings",
        help="Settings file; else SNAPDELTA_SETTINGS, ./snapdelta.toml, ~/.config/snapdelta/snapdelta.toml.",
    ),
]
Format = Annotated[
    str, typer.Option("--format", help="Output format: text, json, markdown, csv, or a format of a plugin.")
]


def handle_errors[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """A SnapdeltaError is a fault of the input, so the user gets its message and exit code 2, not a traceback."""

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return command(*args, **kwargs)
        except SnapdeltaError as error:
            typer.echo(f"error: {error}", err=True)
            raise typer.Exit(2) from error

    return wrapper


@dataclass
class Runtime:
    settings_file: Path | None
    store_option: str | None

    @functools.cached_property
    def settings(self) -> Settings:
        """Read on first use, so that a broken settings file stops only the commands that need the settings."""
        settings = load_settings(self.settings_file, self.store_option)
        for warning in settings.warnings:
            typer.echo(f"warning: {warning}", err=True)
        configure_logging(settings.snapshot.log_level)
        return settings

    @functools.cached_property
    def store(self) -> Store:
        """Opened on first use, so that a command without a store never creates a SQLite file."""
        return open_store_from(self.settings)

    def close(self) -> None:
        if "store" in self.__dict__:
            self.store.close()


def _print_version(value: bool) -> None:
    if value:
        typer.echo(version_line())
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=_print_version, is_eager=True, help="Print the versions and stop."),
    ] = False,
    store: StoreOption = None,
    settings_file: SettingsOption = None,
) -> None:
    # diffsync logs debug lines on stdout unless the application sets a level.
    enable_console_logging(verbosity=0)
    ctx.obj = Runtime(settings_file, store)
    ctx.call_on_close(ctx.obj.close)


@app.command("settings")
@handle_errors
def show_settings(ctx: typer.Context, as_json: Json = False) -> None:
    """Show the settings in use, the file that they come from, and the origin of each value."""
    runtime: Runtime = ctx.obj
    rows = setting_rows(runtime.settings)
    if as_json:
        file = str(runtime.settings.file) if runtime.settings.file else None
        typer.echo(json.dumps({"file": file, "settings": [asdict(row) for row in rows]}, indent=2))
        return
    typer.echo(f"file: {runtime.settings.file or 'none, defaults'}")
    for row in rows:
        origin = f"{row.origin} {row.note}" if row.note else str(row.origin)
        typer.echo(f"{row.key} = {row.value}  ({origin})")


@app.command("sources")
@handle_errors
def list_sources() -> None:
    """List the installed sources, and whether each reads input files or fetches its data."""
    for name, cls in sorted(source_classes().items()):
        typer.echo(f"{name}\t{package_of(SOURCES, name)}\t{'fetches' if cls.fetches else 'files'}")
    for name, error in sorted(broken_sources().items()):
        typer.echo(f"warning: source {name} cannot load: {error}", err=True)


@plugins_app.callback(invoke_without_command=True)
@handle_errors
def list_plugins(ctx: typer.Context, as_json: Json = False) -> None:
    """List the plugins of every extension point, the built-ins included, with their package and status."""
    if ctx.invoked_subcommand is not None:
        return
    reports = plugin_reports()
    if as_json:
        typer.echo(json.dumps([_report_json(report) for report in reports], indent=2))
        return
    for report in reports:
        typer.echo(f"{report.kind}\t{report.name}\t{_package(report)}\t{report.severity}")


@plugins_app.command("new")
@handle_errors
def new_plugin_command(
    name: Annotated[
        str, typer.Argument(help="The name of the plugins, such as acme: the source acme, the store acme://.")
    ],
    kinds: Annotated[
        list[str] | None,
        typer.Option("--kind", help=f"A kind of plugin to write; repeat it for several: {', '.join(KINDS)}."),
    ] = None,
    path: Annotated[Path | None, typer.Option(help="The directory of the package; default: ./snapdelta-NAME.")] = None,
) -> None:
    """Write a plugin package to start from: one working plugin of each kind, its entry point and its tests."""
    target = path or Path(f"snapdelta-{name}")
    for written in new_plugin(name, kinds or ["source"], target):
        typer.echo(f"wrote {written}")
    typer.echo(f"next: cd {target} && uv sync && uv run snapdelta doctor && uv run pytest")


@app.command("doctor")
def doctor(ctx: typer.Context, as_json: Json = False) -> None:
    """Check each plugin against its interface, then the settings, store and inventory; exit code 1 on an error."""
    runtime: Runtime = ctx.obj
    diagnosis = diagnose(runtime.settings_file, runtime.store_option)
    errors, warnings = diagnosis.count(Severity.ERROR), diagnosis.count(Severity.WARNING)
    if as_json:
        checks = [{**asdict(check), "severity": check.severity} for check in diagnosis.checks]
        plugins = [_report_json(report) for report in diagnosis.plugins]
        typer.echo(json.dumps({"errors": errors, "warnings": warnings, "plugins": plugins, "checks": checks}, indent=2))
        raise typer.Exit(0 if diagnosis.healthy else 1)
    typer.echo("plugins:")
    for report in diagnosis.plugins:
        typer.echo(f"  {report.severity:<8} {report.kind} {report.name} ({_package(report)})")
        _print_problems(report.problems)
    typer.echo("setup:")
    for check in diagnosis.checks:
        typer.echo(f"  {check.severity:<8} {check.subject}: {check.detail}")
        _print_problems(check.problems)
    typer.echo(f"{errors} errors, {warnings} warnings")
    raise typer.Exit(0 if diagnosis.healthy else 1)


def _report_json(report: PluginReport) -> dict[str, object]:
    return {**asdict(report), "severity": report.severity}


def _package(report: PluginReport) -> str:
    return f"{report.package} {report.version or ''}".strip() if report.package else "registered in code"


def _print_problems(problems: list[Problem]) -> None:
    for problem in problems:
        typer.echo(f"           {problem.severity}: {problem.message}")


@app.command("models")
@handle_errors
def list_models(
    source: Annotated[str | None, typer.Option(help="Only the models of this source.")] = None,
    as_json: Json = False,
) -> None:
    """List the models of the installed sources: identifiers, attributes, children, and the sources that compare."""
    sets = catalog(source_classes())
    if source is not None:
        require_source(source)
        sets = [found for found in sets if source in found.names]
    if as_json:
        typer.echo(json.dumps([[asdict(item) for item in found.sources] for found in sets], indent=2))
        return
    for found in sets:
        typer.echo(", ".join(f"{item.source} ({'fetches' if item.fetches else 'files'})" for item in found.sources))
        if found.compares_with:
            typer.echo(f"compares with: {', '.join(found.compares_with)}")
        for conflict in found.conflicts:
            typer.echo(f"warning: {conflict}")
        for model in found.models:
            place = f"  child of {model.parent}" if model.parent else ""
            groups = f"  groups: {', '.join(model.groups)}" if model.groups else ""
            typer.echo(f"  {model.name}{place}  id: {model.unique_id}{groups}")
            for item in model.attributes:
                typer.echo(f"    {item.name}: {item.type}")
            for item in model.context:
                typer.echo(f"    {item.name}: {item.type}  (not compared)")
            if model.children:
                typer.echo(f"    children: {', '.join(model.children)}")
        typer.echo("")


@app.command("inventory")
@handle_errors
def list_inventory(
    ctx: typer.Context,
    group: Annotated[str | None, typer.Option(help="Only the hosts of this group.")] = None,
) -> None:
    """List the hosts of the inventory: platform, source, groups and address."""
    settings: Settings = ctx.obj.settings
    inventory = settings.hosts()
    if inventory is None:
        raise SnapdeltaError("no inventory: set [inventory] location in the settings (see docs/inventory.md)")
    for warning in inventory.warnings:
        typer.echo(f"warning: {warning}", err=True)
    names = inventory.members(group) if group else sorted(inventory.hosts)
    for name in names:
        host = inventory.hosts[name]
        source = platform_source(settings, host.platform) or "no source"
        address = f"{host.hostname}:{host.port}" if host.port else host.hostname or ""
        typer.echo(f"{name}\t{host.platform or 'no platform'}\t{source}\t{','.join(host.groups)}\t{address}")


@app.command("snapshot")
@handle_errors
def snapshot_command(
    ctx: typer.Context,
    source: Annotated[
        str | None,
        typer.Option(help="Source, see `snapdelta sources`; with an inventory, the platform of each host chooses it."),
    ] = None,
    files: Files = None,
    targets: Annotated[
        list[str] | None,
        typer.Option("--target", help="Target name; repeat it to fetch several targets at the same time."),
    ] = None,
    groups: Annotated[
        list[str] | None, typer.Option("--group", help="Every host of this inventory group; repeat it for several.")
    ] = None,
    all_targets: Annotated[
        bool, typer.Option(help="Every target that the source lists, or every host of the inventory.")
    ] = False,
    jobs: Annotated[
        int | None, typer.Option(min=1, help="Targets fetched at the same time; default from the settings.")
    ] = None,
    label: Label = None,
    captured_at: Annotated[str | None, typer.Option(help="Capture time, ISO 8601 (UTC without an offset).")] = None,
) -> None:
    """Load the files of one target, or fetch from one or more targets, and save a snapshot of each."""
    runtime: Runtime = ctx.obj
    settings = runtime.settings
    when = _parse_time(captured_at)
    cls = require_source(source) if source else None
    if files or (cls is not None and not cls.fetches):
        if cls is None:
            raise SnapdeltaError("give --source with input files")
        names = target_names(cls, targets or [], all_targets, settings, groups or [])
        if len(names) > 1:
            raise SnapdeltaError("one target at most with input files: the files belong to one target")
        loaded = _load(settings, cls.source, files or [], names[0] if names else None, label, when)
        _print_saved(runtime.store.save_snapshot(loaded), loaded.loaded, loaded.warnings, len(loaded.unmapped))
        return
    if not (targets or groups or all_targets):
        raise SnapdeltaError("the source fetches its data from a target: give --target, --group or --all-targets")
    taken, failures = snapshot_targets(
        runtime.store, settings, source, targets or [], groups or [], all_targets, label, "cli", jobs, when
    )
    for item in taken.values():
        _print_saved(item.reference, item.loaded, item.warnings, item.unmapped)
    for message in failures.values():
        typer.echo(f"error: {message}", err=True)
    if failures:
        raise typer.Exit(2)


def _print_saved(reference: str, loaded: list[str], warnings: list[str], unmapped: int) -> None:
    typer.echo(f"saved {reference}")
    typer.echo(f"loaded: {', '.join(loaded)}")
    for warning in warnings:
        typer.echo(f"warning: {warning}")
    typer.echo(f"unmapped leaves: {unmapped}")


@app.command("list")
@handle_errors
def list_files(
    ctx: typer.Context, target: Annotated[str | None, typer.Option(help="Only the targets whose name has this.")] = None
) -> None:
    """List the snapshots and the outputs of the store."""
    store = ctx.obj.store
    typer.echo("snapshots:")
    for name in store.targets(target or ""):
        for entry in store.snapshot_entries(name):
            typer.echo(f"  {entry.reference}")
    typer.echo("outputs:")
    for output in store.output_entries(target or ""):
        typer.echo(f"  {output.name}")


@app.command("show")
@handle_errors
def show(
    ctx: typer.Context,
    snapshot: Annotated[str, typer.Argument(help="Snapshot TARGET/NAME, name, or TARGET@latest.")],
    model: Annotated[str | None, typer.Option(help="Only this model.")] = None,
) -> None:
    """Print the data of a snapshot."""
    store = ctx.obj.store
    typer.echo(render_snapshot(store.load_snapshot(store.resolve_snapshot(snapshot)), model))


@app.command("diff")
@handle_errors
def diff(
    ctx: typer.Context,
    left: Annotated[str, typer.Argument(help="Left snapshot (before); with targets, its label: @before.")],
    right: Annotated[
        str | None, typer.Argument(help="Right snapshot (after); with targets, its label: @after.")
    ] = None,
    targets: Annotated[
        list[str] | None,
        typer.Option("--target", help="Compare TARGET@LEFT with TARGET@RIGHT for this target; repeat it for several."),
    ] = None,
    groups: Annotated[
        list[str] | None, typer.Option("--group", help="Each host of this inventory group; repeat it for several.")
    ] = None,
    all_targets: Annotated[bool, typer.Option(help="Each target of the store that has a LEFT snapshot.")] = False,
    jobs: Annotated[
        int | None, typer.Option(min=1, help="With targets and --live: targets fetched at the same time.")
    ] = None,
    label: Label = None,
    models: Annotated[
        list[str] | None, typer.Option("--model", help="Compare only this model; repeat it for several.")
    ] = None,
    live: Annotated[
        bool, typer.Option("--live", help="Fetch the target again with the source of LEFT as the right side.")
    ] = False,
    profile: Annotated[
        str | None, typer.Option(help="Compare profile of the settings: [profiles.NAME]; see `snapdelta settings`.")
    ] = None,
    no_save: Annotated[
        bool, typer.Option("--no-save", help="Do not save the output, nor the snapshot of --live.")
    ] = False,
    title: Annotated[str | None, typer.Option(help="A short title of the diff, shown in the lists.")] = None,
    description: Annotated[str | None, typer.Option(help="What the diff is about: the change, the ticket.")] = None,
    expect: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help="The expected changes (YAML or JSON); exit code 1 when the diff is not them.",
        ),
    ] = None,
    output_format: Format = "text",
) -> None:
    """Compare a snapshot with another, or with its target now (--live); exit code 1 on a difference.

    With --target, --group or --all-targets, compare TARGET@LEFT with TARGET@RIGHT (or the target now) for each
    target: `snapdelta diff --group core @before @after`; exit code 2 when a target fails. To compare with input
    files, take a snapshot of them first: `snapdelta snapshot FILES --source S --label after`.
    """
    store = ctx.obj.store
    chosen = renderer(output_format)
    if (right is not None) == live:
        raise SnapdeltaError("give one right side: a RIGHT snapshot, or --live")
    settings: Settings = ctx.obj.settings
    expected = read_expected(expect) if expect else None
    common = {
        "models": models,
        "profile": profile,
        "title": title,
        "description": description,
        "expected": expected,
        "origin": "cli",
    }
    if targets or groups or all_targets:
        if not left.startswith("@") or (right is not None and not right.startswith("@")):
            raise SnapdeltaError("with targets, LEFT and RIGHT are labels: @before @after, or @latest")
        results = diff_targets(
            store,
            settings,
            left,
            right,
            targets=targets or [],
            groups=groups or [],
            all_targets=all_targets,
            live=live,
            live_label=label or "live",
            save=not no_save,
            jobs=jobs,
            **common,
        )
        _print_target_diffs(results, chosen)
        return
    done = diff_snapshots(
        store, settings, left, right, live=live, live_label=label or "live", save=not no_save, **common
    )
    for saved in (done.live, done.name):
        if saved:
            typer.echo(f"saved {saved}", err=True)
    _print_output(done.output, chosen)


def _print_target_diffs(results: dict[str, TargetDiff], chosen: Renderer) -> None:
    """The outputs in the chosen format, then a summary on stderr; exit code 2 on a failure, 1 on a difference."""
    outputs = [item.output for item in results.values() if item.output is not None]
    if outputs:
        typer.echo(chosen.many(outputs).rstrip("\n"))
    for item in results.values():
        for saved in (item.live, item.name):
            if saved:
                typer.echo(f"saved {saved}", err=True)
    changed = [item.target for item in results.values() if item.changed]
    same = [item.target for item in results.values() if item.output is not None and not item.changed]
    failed = {item.target: item.error for item in results.values() if item.error}
    typer.echo(f"changed: {', '.join(changed) or 'none'}", err=True)
    typer.echo(f"unchanged: {', '.join(same) or 'none'}", err=True)
    checked = [item for item in results.values() if item.as_expected is not None]
    if checked:
        off = [item.target for item in checked if not item.as_expected]
        typer.echo(f"not as expected: {', '.join(off) or 'none'}", err=True)
    for message in failed.values():
        typer.echo(f"error: {message}", err=True)
    wrong = any(not item.as_expected for item in checked) if checked else bool(changed)
    raise typer.Exit(2 if failed else 1 if wrong else 0)


@app.command("output")
@handle_errors
def show_output(
    ctx: typer.Context,
    output: Annotated[str, typer.Argument(help="Output name.")],
    output_format: Format = "text",
) -> None:
    """Print a saved output. Exit code 1 when it holds differences."""
    store = ctx.obj.store
    chosen = renderer(output_format)
    _print_output(store.load_output(store.resolve_output(output)), chosen)


@app.command("activity")
@handle_errors
def show_activity(
    ctx: typer.Context,
    run_id: Annotated[str | None, typer.Argument(metavar="RUN", help="One run, with its log.")] = None,
    kind: Annotated[str | None, typer.Option(help="Only the runs of this kind: snapshot or diff.")] = None,
    limit: Annotated[int, typer.Option(min=1, help="How many runs, newest first.")] = 20,
    as_json: Json = False,
) -> None:
    """List the runs of the store (snapshots and diffs, from the CLI, the web UI, the API and MCP), or show one."""
    store: Store = ctx.obj.store
    if run_id is not None:
        run = store.load_run(run_id)
        if as_json:
            typer.echo(run.model_dump_json(indent=2))
            return
        typer.echo(f"{run.id}: {run.kind} from {run.origin}, {run.status}, {run.duration_s:.2f} s")
        typer.echo(f"targets: {', '.join(run.targets) or 'none'}")
        for reference in run.saved:
            typer.echo(f"saved: {reference}")
        for name in run.outputs:
            typer.echo(f"diff: {name}")
        for name, result in sorted(run.notified.items()):
            typer.echo(f"notifier {name}: {result}")
        for target, error in sorted(run.errors.items()):
            typer.echo(f"error: {'the run' if target == '*' else target}: {error}")
        for line in run.log:
            typer.echo(f"{line.at:%H:%M:%S}  {line.level:<8} {line.target or '-':<12} {line.logger}: {line.message}")
        return
    runs = store.runs(limit, kind)
    if as_json:
        typer.echo(json.dumps([run.model_dump(mode="json", exclude={"log"}) for run in runs], indent=2))
        return
    for run in runs:
        result = f"{len(run.saved)} saved" + (f", diffs {', '.join(run.outputs)}" if run.outputs else "")
        typer.echo(f"{run.id}  {run.origin:<4} {run.status:<8} {', '.join(run.targets)}  {result}")


@app.command("info")
@handle_errors
def info(ctx: typer.Context) -> None:
    """Show the store backend, location, counts and size."""
    store = ctx.obj.store
    details = store.info()
    typer.echo(f"backend: {details.backend}")
    typer.echo(f"location: {details.location}")
    typer.echo(f"targets: {details.targets}")
    typer.echo(f"snapshots: {details.snapshots}")
    typer.echo(f"outputs: {details.outputs}")
    typer.echo(f"size: {details.size_bytes} bytes")


@app.command("describe")
@handle_errors
def describe_output(
    ctx: typer.Context,
    output: Annotated[str, typer.Argument(help="Output name.")],
    title: Annotated[str | None, typer.Option(help="The new title; an empty text removes it.")] = None,
    description: Annotated[str | None, typer.Option(help="The new description; an empty text removes it.")] = None,
) -> None:
    """Give a saved diff a title and a description, or change them."""
    store = ctx.obj.store
    name = store.resolve_output(output)
    current = store.load_output(name)
    changed = store.describe_output(
        name,
        current.title if title is None else title,
        current.description if description is None else description,
    )
    typer.echo(f"{name}: {changed.title or 'no title'}")


@app.command("accept")
@handle_errors
def accept_changes(
    ctx: typer.Context,
    output: Annotated[str, typer.Argument(help="Output name.")],
    keys: Annotated[
        list[str] | None, typer.Argument(help="MODEL:UNIQUE_ID of each changed object; see `snapdelta output`.")
    ] = None,
    every: Annotated[bool, typer.Option("--all", help="Every change of the output.")] = False,
    by: Annotated[str | None, typer.Option(help="Who accepts; default: the login name.")] = None,
    comment: Annotated[str | None, typer.Option(help="Why the change is right.")] = None,
    undo: Annotated[bool, typer.Option("--undo", help="Withdraw the acceptance.")] = False,
) -> None:
    """Accept changes of a saved diff after a review, or withdraw the acceptance."""
    store = ctx.obj.store
    name = store.resolve_output(output)
    chosen = store.load_output(name).change_keys() if every else keys or []
    if not chosen:
        raise SnapdeltaError("give the keys of the changes, or --all")
    review = None if undo else new_review(by or getpass.getuser(), comment)
    for key in chosen:
        store.review_output(name, key, review)
    done, total = store.load_output(name).review_counts()
    typer.echo(f"{name}: accepted {done} of {total} changes")


@delete_app.command("snapshot")
@handle_errors
def delete_snapshot(
    ctx: typer.Context,
    references: Annotated[list[str], typer.Argument(help="Snapshots: TARGET/NAME, name, or TARGET@latest.")],
) -> None:
    """Delete snapshots; the outputs that use them stay, with their changed values only."""
    store = ctx.obj.store
    for reference in references:
        resolved = store.resolve_snapshot(reference)
        store.delete_snapshot(resolved)
        typer.echo(f"deleted {resolved}")


@delete_app.command("output")
@handle_errors
def delete_output(ctx: typer.Context, names: Annotated[list[str], typer.Argument(help="Output names.")]) -> None:
    """Delete saved outputs."""
    store = ctx.obj.store
    for name in names:
        resolved = store.resolve_output(name)
        store.delete_output(resolved)
        typer.echo(f"deleted {resolved}")


@app.command("prune")
@handle_errors
def prune(
    ctx: typer.Context,
    target: Annotated[str | None, typer.Option(help="Only this target.")] = None,
    keep: Annotated[int | None, typer.Option(min=0, help="Keep the N newest snapshots of each target.")] = None,
    older_than: Annotated[
        int | None, typer.Option(min=0, help="Delete what is older than this number of days.")
    ] = None,
    outputs: Annotated[
        bool, typer.Option("--outputs", help="Also delete the outputs older than --older-than.")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only print what would be deleted.")] = False,
) -> None:
    """Delete old snapshots (and outputs): a snapshot goes when no rule given keeps it."""
    store = ctx.obj.store
    cutoff = age_cutoff(older_than) if older_than is not None else None
    plan = store.prune_plan(target=target, keep=keep, older_than=cutoff, outputs=outputs)
    for reference in plan.snapshots:
        typer.echo(f"snapshot {reference}")
    for name in plan.outputs:
        typer.echo(f"output {name}")
    counts = f"{len(plan.snapshots)} snapshots and {len(plan.outputs)} outputs"
    if dry_run:
        typer.echo(f"would delete {counts}")
        return
    store.apply(plan)
    typer.echo(f"deleted {counts}")


def _print_output(output: Output, chosen: Renderer) -> None:
    """With expected changes, exit code 1 when the diff is not them; else exit code 1 on a difference."""
    typer.echo(chosen.render(output).rstrip("\n"))
    if output.expectations is not None:
        raise typer.Exit(0 if output.expectations.as_expected else 1)
    raise typer.Exit(1 if output.has_differences() else 0)


def _load(
    settings: Settings,
    source: str,
    files: list[Path],
    target: str | None,
    label: str | None,
    captured_at: datetime | None,
) -> Source:
    cls = require_source(source)
    if cls.fetches and not target:
        raise SnapdeltaError(f"source {source!r} fetches its data from a target: give --target")
    if not cls.fetches and not files:
        raise SnapdeltaError(f"source {source!r} reads input files: give the input files")
    return load_source(cls, files, target, label, captured_at, settings.source(cls))


def _parse_time(text: str | None) -> datetime | None:
    if text is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise SnapdeltaError(f"--captured-at {text!r} is not an ISO 8601 time") from error
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
