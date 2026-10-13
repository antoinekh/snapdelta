"""The MCP server: the tools of snapdelta for AI agents, over stdio (`snapdelta-mcp`) or HTTP (`/mcp` of the web UI)."""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from diffsync.logging import enable_console_logging
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from snapdelta.about import package_version
from snapdelta.activity import Run, configure_logging
from snapdelta.catalog import catalog
from snapdelta.cli import SettingsOption, StoreOption
from snapdelta.errors import SnapdeltaError
from snapdelta.output import change_key, new_review
from snapdelta.render import change_rows
from snapdelta.settings import Profile, load_settings
from snapdelta.source import source_classes
from snapdelta.store import open_store_from
from snapdelta.take import diff_snapshots, snapshot_targets

from .api import DiffItem, ReviewState, RunItem, SnapshotInfo, SnapshotResult, SourceInfo, review_state
from .state import AppState

GUIDE = (Path(__file__).parent / "mcp_guide.md").read_text(encoding="utf-8")
INSTRUCTIONS = (
    "snapdelta snapshots devices and compares the snapshots. To check a change: take_snapshots with the label "
    "before, the change, take_snapshots with the label after, then compare TARGET@before with TARGET@after and read "
    "the changes. Read the resource snapdelta://guide first."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# A snapshot reads the devices: open world, but it changes nothing on them.
FETCHES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
PAGE = 100
Reference = Annotated[str, Field(description="A snapshot: TARGET/NAME, TARGET@latest, or TARGET@LABEL (r1@before).")]
DiffName = Annotated[str, Field(description="The name of a saved diff, as compare or list_diffs give it.")]


class TargetItem(BaseModel):
    name: str
    snapshots: int
    platform: str | None = None
    groups: list[str] = []


class Change(BaseModel):
    """One change: an object of one side only, or one changed attribute of an object."""

    key: str = Field(description="MODEL:UNIQUE_ID, for accept_changes.")
    model: str
    object: str
    parents: list[str]
    change: str = Field(description="only-left (removed), only-right (added) or changed")
    attribute: str | None
    left: Any
    right: Any
    accepted: bool


class ChangePage(BaseModel):
    name: str
    title: str | None
    has_differences: bool
    summary: dict[str, int]
    changes: list[Change]
    total: int = Field(description="Every change of the diff, or of the model asked for.")
    next_offset: int | None = Field(description="The offset of the next page; none on the last page.")


class ModelItem(BaseModel):
    name: str
    parent: str | None
    identifiers: list[str]
    attributes: list[str]
    not_compared: list[str]
    children: list[str]
    sources: list[str]


class ProfileItem(BaseModel):
    name: str
    rules: Profile


def create_mcp(state: AppState) -> MCPServer:
    """The server and its tools, on the store and the settings of `state`."""
    server = MCPServer(
        name="snapdelta",
        title="snapdelta",
        description="Snapshots of devices and the diff between two of them.",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/antoinekh/snapdelta",
        version=package_version("snapdelta-web"),
    )

    def tool(annotations: ToolAnnotations) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a tool whose snapdelta errors reach the agent as a message: a wrong name, a missing target."""

        def register(func: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(func)
            def answer(*args: Any, **kwargs: Any) -> Any:
                try:
                    return func(*args, **kwargs)
                except SnapdeltaError as error:
                    raise ToolError(str(error)) from error

            return server.tool(annotations=annotations)(answer)

        return register

    @server.resource("snapdelta://guide", name="guide", title="How snapdelta works", mime_type="text/markdown")
    def guide() -> str:
        """The words of snapdelta, the steps to check a change, and the rules for an agent."""
        return GUIDE

    @tool(READ_ONLY)
    def list_sources() -> list[SourceInfo]:
        """The installed sources; a source that fetches can take snapshots of its targets."""
        return [
            SourceInfo(name=name, distribution=cls.distribution, fetches=cls.fetches)
            for name, cls in sorted(source_classes().items())
        ]

    @tool(READ_ONLY)
    def list_targets(query: str = "", group: str = "") -> list[TargetItem]:
        """The targets of the inventory and of the store whose name contains `query`, in `group` when given."""
        inventory = state.settings.hosts()
        names = set(state.store.targets(query))
        if inventory is not None:
            names |= {name for name in inventory.hosts if query.lower() in name.lower()}
        if group:
            if inventory is None:
                raise SnapdeltaError("a group needs an inventory")
            names &= set(inventory.members(group))
        items = []
        for name in sorted(names):
            host = inventory.host(name) if inventory else None
            count = len(state.store.snapshot_entries(name))
            platform, groups = (host.platform, list(host.groups)) if host else (None, [])
            items.append(TargetItem(name=name, snapshots=count, platform=platform, groups=groups))
        return items

    @tool(READ_ONLY)
    def list_snapshots(target: str, limit: int = 20) -> list[SnapshotInfo]:
        """The snapshots of one target, newest first."""
        return [
            SnapshotInfo(
                reference=entry.reference, target=entry.target, label=entry.label, captured_at=entry.captured_at
            )
            for entry in state.store.snapshot_entries(target)[:limit]
        ]

    @tool(FETCHES)
    def take_snapshots(
        targets: Annotated[list[str] | None, Field(description="Target names, as list_targets gives them.")] = None,
        groups: Annotated[list[str] | None, Field(description="Inventory groups: every host of each.")] = None,
        all_targets: Annotated[bool, Field(description="Every host of the inventory.")] = False,
        label: Annotated[str | None, Field(description="The label of the snapshots: before, after.")] = None,
        source: Annotated[str | None, Field(description="One source for all; none: the platform of each.")] = None,
    ) -> SnapshotResult:
        """Fetch the targets at the same time and save a snapshot of each, with `label` (before, after).

        Without `source`, each target uses the source of its platform in the inventory.
        """
        taken, failures = snapshot_targets(
            state.store, state.settings, source, targets or [], groups or [], all_targets, label, origin="mcp"
        )
        saved = {target: item.reference for target, item in taken.items()}
        return SnapshotResult(ok=not failures, saved=saved, failed=failures)

    @tool(FETCHES)
    def compare(
        left: Reference,
        right: Annotated[str | None, Field(description="The right snapshot; none with live.")] = None,
        live: Annotated[bool, Field(description="Fetch the target of left now as the right side.")] = False,
        target: Annotated[str | None, Field(description="With live: another target than the one of left.")] = None,
        models: Annotated[list[str] | None, Field(description="Only these models; none: all.")] = None,
        profile: Annotated[str | None, Field(description="A profile of the settings, see list_profiles.")] = None,
        title: Annotated[str | None, Field(description="What the diff is about: CHG-1042: VLAN 20, r1.")] = None,
        description: Annotated[str | None, Field(description="More words about the change.")] = None,
        save: Annotated[bool, Field(description="Save the diff, and with live the new snapshot.")] = True,
    ) -> ChangePage:
        """Compare the snapshot `left` with `right`, or with its target now (`live`); the first page of changes.

        References: TARGET/NAME, TARGET@latest, TARGET@LABEL. `profile` names rules of the settings (list_profiles).
        """
        done = diff_snapshots(
            state.store,
            state.settings,
            left,
            right,
            live=live,
            target=target,
            models=models,
            profile=profile,
            title=title,
            description=description,
            save=save,
            origin="mcp",
        )
        return _page(done.name or "not saved", done.output, None, 0, PAGE)

    @tool(READ_ONLY)
    def read_diff(
        name: DiffName,
        model: Annotated[str | None, Field(description="Only the changes of this model.")] = None,
        offset: Annotated[int, Field(description="Where the page starts: next_offset of the page before.")] = 0,
        limit: Annotated[int, Field(description="How many changes per page.")] = PAGE,
    ) -> ChangePage:
        """One page of the changes of a saved diff, of one model when given."""
        resolved = state.store.resolve_output(name)
        return _page(resolved, state.store.load_output(resolved), model, offset, limit)

    @tool(READ_ONLY)
    def list_diffs(query: str = "", limit: int = 20) -> list[DiffItem]:
        """The newest saved diffs whose left or right target contains `query`."""
        return [
            DiffItem.of(entry.name, state.store.load_output(entry.name))
            for entry in state.store.output_entries(query)[:limit]
        ]

    @tool(WRITES)
    def accept_changes(
        name: DiffName,
        keys: Annotated[list[str] | None, Field(description="MODEL:UNIQUE_ID of each change, from read_diff.")] = None,
        all_changes: Annotated[bool, Field(description="Every change of the diff.")] = False,
        comment: Annotated[str | None, Field(description="Why the change is right.")] = None,
        by: Annotated[str, Field(description="Who accepts: the agent and the person it works for.")] = "agent",
    ) -> ReviewState:
        """Accept changes of a saved diff after checking them against the intent of the change; keys from read_diff."""
        resolved = state.store.resolve_output(name)
        output = state.store.load_output(resolved)
        chosen = output.change_keys() if all_changes else keys or []
        if not chosen:
            raise SnapdeltaError("give the keys of the changes, or all_changes")
        review = new_review(by, comment)
        for key in chosen:
            output = state.store.review_output(resolved, key, review)
        return review_state(output)

    @tool(WRITES)
    def describe_diff(name: str, title: str | None = None, description: str | None = None) -> DiffItem:
        """Give a saved diff a title (CHG-1042: VLAN 20, r1) and a description."""
        resolved = state.store.resolve_output(name)
        current = state.store.load_output(resolved)
        chosen_title = current.title if title is None else title
        chosen_description = current.description if description is None else description
        return DiffItem.of(resolved, state.store.describe_output(resolved, chosen_title, chosen_description))

    @tool(READ_ONLY)
    def list_profiles() -> list[ProfileItem]:
        """The compare profiles of the settings, with their rules."""
        return [ProfileItem(name=name, rules=rules) for name, rules in sorted(state.settings.profiles.items())]

    @tool(READ_ONLY)
    def list_models() -> list[ModelItem]:
        """The models of the installed sources: identifiers, compared attributes, fields kept but never compared."""
        items = []
        for found in catalog(source_classes()):
            for model in found.models:
                items.append(
                    ModelItem(
                        name=model.name,
                        parent=model.parent,
                        identifiers=[item.name for item in model.identifiers],
                        attributes=[item.name for item in model.attributes],
                        not_compared=[item.name for item in model.context],
                        children=model.children,
                        sources=found.names,
                    )
                )
        return items

    @tool(READ_ONLY)
    def list_runs(kind: str | None = None, limit: int = 20) -> list[RunItem]:
        """The newest runs (snapshot or diff) from the CLI, the web UI, the API and MCP, without their log."""
        return [RunItem.of(run) for run in state.store.runs(limit, kind)]

    @tool(READ_ONLY)
    def read_run(run_id: str) -> Run:
        """One run, with the log messages of its loads: why a target failed, what a source did."""
        return state.store.load_run(run_id)

    @server.prompt(title="Check a change")
    def check_change(targets: str, change: str) -> str:
        """Snapshot the targets before and after a change, compare them, and review the changes against the intent."""
        return (
            f"Check this change on {targets}: {change}\n\n"
            "Follow the steps of the resource snapdelta://guide: take_snapshots with the label before, wait until "
            "the change is applied (ask me when it is), take_snapshots with the label after, then compare "
            "TARGET@before with TARGET@after for each target, with a title that names the change. Accept the "
            "changes that the change explains, with a comment. List every other change for me, with its target."
        )

    @server.prompt(title="Review a diff")
    def review_diff(name: str, intent: str = "") -> str:
        """Read every change of a saved diff and sort them into expected and unexpected."""
        purpose = f" The intent of the change: {intent}." if intent else ""
        return (
            f"Review the saved diff {name}.{purpose} Read all its changes with read_diff, page by page. "
            "Accept the expected ones with accept_changes and a short comment; list the unexpected ones for me."
        )

    return server


def _page(name: str, output: Any, model: str | None, offset: int, limit: int) -> ChangePage:
    rows = [row for row in change_rows(output) if model is None or row.model == model]
    window = rows[offset : offset + max(limit, 1)]
    end = offset + len(window)
    return ChangePage(
        name=name,
        title=output.title,
        has_differences=output.has_differences(),
        summary=output.summary,
        changes=[_change(row, output.reviews) for row in window],
        total=len(rows),
        next_offset=end if end < len(rows) else None,
    )


def _change(row: Any, reviews: dict[str, Any]) -> Change:
    key = change_key(row.model, row.name)
    return Change(
        key=key,
        model=row.model,
        object=row.name,
        parents=list(row.parents),
        change=row.change,
        attribute=row.attribute,
        left=row.left,
        right=row.right,
        accepted=key in reviews,
    )


app = typer.Typer(add_completion=False)


@app.command()
def main(
    store: StoreOption = None,
    settings_file: SettingsOption = None,
) -> None:
    """Serve the snapdelta tools to an AI agent over stdio (MCP); for HTTP, use /mcp of snapdelta-web."""
    # Over stdio, stdout carries the protocol: every log goes to stderr, diffsync's included.
    enable_console_logging(verbosity=0)
    try:
        settings = load_settings(settings_file, store)
        configure_logging(settings.snapshot.log_level)
        state = AppState(settings, open_store_from(settings), settings_file, store)
    except SnapdeltaError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    create_mcp(state).run()
