"""The JSON API, for a pipeline or an AI agent: take snapshots, compare them, read and describe the diffs."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import FastAPI, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from snapdelta.about import components, package_version, plugins
from snapdelta.activity import Run, RunStatus
from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.output import Output, Review, Side, new_review
from snapdelta.render import renderer
from snapdelta.snapshot import SnapshotFile
from snapdelta.source import source_classes
from snapdelta.take import diff_snapshots, snapshot_targets

from .access import LruCache
from .state import AppState

PREFIX = "/api/v1"
# One call returns a page, not the whole store; the MCP tools use the same bound.
MAX_LIMIT = 500


class Component(BaseModel):
    name: str
    version: str
    sources: list[str] = []


class VersionInfo(BaseModel):
    components: list[Component]
    plugins: list[Component]


class SourceInfo(BaseModel):
    name: str
    distribution: str | None
    fetches: bool


class SnapshotInfo(BaseModel):
    reference: str
    target: str
    label: str | None
    captured_at: datetime


class SnapshotRequest(BaseModel):
    source: str | None = Field(default=None, description="The source; none: the platform of each inventory host.")
    targets: list[str] = Field(default=[], description="Target names.")
    groups: list[str] = Field(default=[], description="Inventory groups: every host of each group.")
    all_targets: bool = Field(default=False, description="Every target of the source, or every inventory host.")
    label: str | None = Field(default=None, description="The label of the snapshots, for example before.")


class SnapshotResult(BaseModel):
    ok: bool = Field(description="True when every target was saved.")
    saved: dict[str, str] = Field(description="The reference of the snapshot of each saved target.")
    failed: dict[str, str] = Field(description="The error of each failed target.")


class DiffRequest(BaseModel):
    left: str = Field(description="A snapshot: TARGET/NAME, TARGET@latest or TARGET@LABEL (newest with that label).")
    right: str | None = Field(default=None, description="The right snapshot; none with live.")
    live: bool = Field(default=False, description="Fetch the target of left now as the right side.")
    target: str | None = Field(default=None, description="With live: another target than the one of left.")
    models: list[str] = Field(default=[], description="Only these models; none: every model of both sides.")
    profile: str | None = Field(default=None, description="A compare profile of the settings.")
    title: str | None = None
    description: str | None = None
    save: bool = Field(default=True, description="Save the output, and with live the new snapshot.")


class DiffResult(BaseModel):
    name: str | None = Field(description="The name of the saved output; none with save false.")
    url: str | None = Field(description="The page of the diff in the web UI.")
    has_differences: bool
    summary: dict[str, int]
    left: Side
    right: Side
    title: str | None
    description: str | None
    output: Output | None = Field(default=None, description="The whole output, with include_output.")


class DiffItem(BaseModel):
    name: str
    created_at: datetime
    title: str | None
    left: Side
    right: Side
    has_differences: bool
    summary: dict[str, int]
    accepted: int = Field(description="How many changed objects a person accepted.")
    changes: int = Field(description="How many objects have a change of their own.")

    @classmethod
    def of(cls, name: str, output: Output) -> Self:
        accepted, changes = output.review_counts()
        return cls(
            name=name,
            created_at=output.created_at,
            title=output.title,
            left=output.left,
            right=output.right,
            has_differences=output.has_differences(),
            summary=output.summary,
            accepted=accepted,
            changes=changes,
        )


class RunItem(BaseModel):
    """A run without its log: what the activity list shows."""

    id: str
    kind: str
    origin: str
    user: str | None
    started_at: datetime
    duration_s: float
    status: RunStatus
    targets: list[str]
    saved: list[str]
    output: str | None
    errors: dict[str, str]
    log_lines: int

    @classmethod
    def of(cls, run: Run) -> Self:
        values = run.model_dump(exclude={"log", "ended_at"})
        return cls(**values, duration_s=run.duration_s, log_lines=len(run.log))


class ReviewRequest(BaseModel):
    key: str = Field(description="MODEL:UNIQUE_ID of a changed object, as in the reviews of the output.")
    accepted: bool = Field(default=True, description="False withdraws the acceptance.")
    by: str | None = Field(default=None, description="Who accepts.")
    comment: str | None = Field(default=None, description="Why the change is right.")


class ReviewState(BaseModel):
    accepted: int
    changes: int
    to_review: list[str] = Field(description="The keys of the changes that nobody accepted yet.")
    reviews: dict[str, Review]


class DescribeRequest(BaseModel):
    title: str | None = Field(default=None, description="The new title; none keeps it, an empty text removes it.")
    description: str | None = Field(default=None, description="The new description; none keeps it.")


def create_api(state: AppState, row_cache: LruCache[Any, Any]) -> FastAPI:
    """The API application, mounted under PREFIX; its OpenAPI description is at PREFIX/openapi.json."""
    api = FastAPI(
        title="snapdelta API",
        version=package_version("snapdelta-web"),
        description="Take snapshots, compare them and read the diffs. With a token, send Authorization: Bearer TOKEN.",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    @api.exception_handler(SnapdeltaError)
    async def snapdelta_error(request: Request, error: SnapdeltaError) -> JSONResponse:
        return JSONResponse({"error": str(error)}, status_code=404 if isinstance(error, NotFoundError) else 400)

    @api.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # The details stay in the server log: they can hold paths and data of the store.
        return JSONResponse({"error": "unexpected error: see the log of snapdelta-web"}, status_code=500)

    @api.get("/docs", include_in_schema=False)
    def docs(request: Request) -> HTMLResponse:
        # The Swagger UI files come from this server, so that the page works with no internet access.
        return get_swagger_ui_html(
            openapi_url=f"{request.scope.get('root_path', '')}/openapi.json",
            title="snapdelta API",
            swagger_js_url="/static/vendor/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/static/vendor/swagger-ui/swagger-ui.css",
            swagger_favicon_url="/static/favicon.svg",
        )

    @api.get("/version")
    def version() -> VersionInfo:
        """The versions of snapdelta, the web UI, diffsync, Python and the plugins."""
        return VersionInfo(
            components=[Component(**vars(item)) for item in components(["snapdelta-web"])],
            plugins=[Component(**vars(item)) for item in plugins()],
        )

    @api.get("/sources")
    def sources() -> list[SourceInfo]:
        """The installed sources."""
        return [
            SourceInfo(name=name, distribution=cls.distribution, fetches=cls.fetches)
            for name, cls in sorted(source_classes().items())
        ]

    @api.get("/targets")
    def targets(q: str = "") -> list[str]:
        """The targets that have snapshots in the store, whose name contains q."""
        return state.store.targets(q)

    @api.get("/snapshots")
    def snapshots(target: str) -> list[SnapshotInfo]:
        """The snapshots of one target, newest first."""
        return [
            SnapshotInfo(
                reference=entry.reference, target=entry.target, label=entry.label, captured_at=entry.captured_at
            )
            for entry in state.store.snapshot_entries(target)
        ]

    @api.get("/snapshots/{target}/{name}")
    def snapshot(target: str, name: str) -> SnapshotFile:
        """The whole snapshot document: metadata and data."""
        return state.store.read_snapshot(state.store.resolve_snapshot(f"{target}/{name}"))

    @api.post("/snapshots")
    def take(request: SnapshotRequest) -> SnapshotResult:
        """Fetch the targets, at the same time, and save a snapshot of each; the call returns when all are done."""
        taken, failures = snapshot_targets(
            state.store,
            state.settings,
            request.source,
            request.targets,
            request.groups,
            request.all_targets,
            request.label,
            origin="api",
        )
        saved = {target: item.reference for target, item in taken.items()}
        return SnapshotResult(ok=not failures, saved=saved, failed=failures)

    @api.post("/diffs")
    def run_diff(request: DiffRequest, include_output: bool = False) -> DiffResult:
        """Compare two snapshots, or a snapshot and its target now; has_differences tells the result."""
        done = diff_snapshots(
            state.store,
            state.settings,
            request.left,
            request.right,
            live=request.live,
            target=request.target,
            models=request.models,
            profile=request.profile,
            title=request.title,
            description=request.description,
            save=request.save,
            origin="api",
        )
        output, name = done.output, done.name
        return DiffResult(
            name=name,
            url=f"/diffs/{name}" if name else None,
            has_differences=output.has_differences(),
            summary=output.summary,
            left=output.left,
            right=output.right,
            title=output.title,
            description=output.description,
            output=output if include_output else None,
        )

    @api.get("/diffs")
    def diffs(q: str = "", limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50) -> list[DiffItem]:
        """The newest saved diffs, whose left or right target contains q."""
        return [
            DiffItem.of(entry.name, state.store.load_output(entry.name))
            for entry in state.store.output_entries(q)[:limit]
        ]

    @api.get("/diffs/{name}", response_model=None)
    def diff(name: str, format: Literal["json", "text", "markdown", "csv"] = "json") -> Response:
        """A saved diff: the output document, or the same text as `snapdelta output --format`."""
        output = state.store.load_output(state.store.resolve_output(name))
        if format == "json":
            return Response(output.model_dump_json(by_alias=True), media_type="application/json")
        chosen = renderer(format)
        return PlainTextResponse(chosen.render(output), media_type=chosen.media_type)

    @api.patch("/diffs/{name}")
    def describe(name: str, request: DescribeRequest) -> DiffItem:
        """Change the title or the description of a saved diff."""
        resolved = state.store.resolve_output(name)
        current = state.store.load_output(resolved)
        title = current.title if request.title is None else request.title
        description = current.description if request.description is None else request.description
        output = state.store.describe_output(resolved, title, description)
        row_cache.discard((state.store.location, resolved))
        return DiffItem.of(resolved, output)

    @api.get("/runs")
    def runs(kind: str | None = None, limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50) -> list[RunItem]:
        """The newest runs (snapshot or diff), from the CLI, the web UI, the API and MCP."""
        return [RunItem.of(run) for run in state.store.runs(limit, kind)]

    @api.get("/runs/{run_id}")
    def run(run_id: str) -> Run:
        """One run, with the log messages of its loads."""
        return state.store.load_run(run_id)

    @api.get("/diffs/{name}/reviews")
    def reviews(name: str) -> ReviewState:
        """The accepted changes of a saved diff, and the ones still to review."""
        return review_state(state.store.load_output(state.store.resolve_output(name)))

    @api.post("/diffs/{name}/reviews")
    def review(name: str, request: ReviewRequest) -> ReviewState:
        """Accept one change of a saved diff after a review, or withdraw the acceptance."""
        resolved = state.store.resolve_output(name)
        given = new_review(request.by, request.comment) if request.accepted else None
        output = state.store.review_output(resolved, request.key, given)
        row_cache.discard((state.store.location, resolved))
        return review_state(output)

    return api


def review_state(output: Output) -> ReviewState:
    accepted, changes = output.review_counts()
    to_review = [key for key in output.change_keys() if key not in output.reviews]
    return ReviewState(accepted=accepted, changes=changes, to_review=to_review, reviews=output.reviews)
