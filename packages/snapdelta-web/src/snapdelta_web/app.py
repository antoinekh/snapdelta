"""The web application: routes, error pages, templates and static files."""

import functools
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from snapdelta.about import CHANGELOG_URL, DOCS_URL, ISSUES_URL, PROJECT_URL, components, package_version, plugins
from snapdelta.activity import acting_user, configure_logging
from snapdelta.catalog import catalog
from snapdelta.doctor import plugin_reports
from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.inventory import INVENTORIES
from snapdelta.output import Output, common_models, new_review, note_line, skipped_models
from snapdelta.plugins import discover, extension_points
from snapdelta.render import renderer, renderers
from snapdelta.runners import RUNNERS
from snapdelta.secrets import SECRETS
from snapdelta.settings import Settings, SettingsError, save_settings, user_settings_path
from snapdelta.snapshot import top_level_models
from snapdelta.source import Source, broken_sources, source_classes
from snapdelta.store import LATEST, PrunePlan, SnapshotQuery, Store, age_cutoff
from snapdelta.take import diff_snapshots, diff_targets, plan_sources, platform_source, target_names
from snapdelta.ticket import normalize_ticket

from . import views
from .access import LruCache, SnapshotCache
from .api import PREFIX as API_PREFIX
from .api import create_api
from .auth import (
    AUTH,
    OPEN_PATHS,
    SESSION_COOKIE,
    SESSION_TTL_S,
    Access,
    AuthBackend,
    User,
    auth_backend,
    safe_next,
    session_user,
    session_value,
)
from .jobs import TakeJobs
from .mcp_server import GUIDE as MCP_GUIDE
from .mcp_server import create_mcp
from .settings_form import NEW_PROFILE_NAME, FormGroup, errors_by_field, form_groups, parse_form, profile_groups
from .state import AppState

HERE = Path(__file__).parent
# The value of the Source list that lets the inventory choose the source of each host by its platform.
BY_PLATFORM = ""
# The Admin tabs that edit the settings file.
SETTINGS, PROFILES = "settings", "profiles"
# A filter of Activity reads this many of the newest runs, since a store lists its runs without a filter of its own.
ACTIVITY_WINDOW = 1000


@functools.cache
def asset_url(name: str) -> str:
    """The URL of a static file with a hash of its content, so that a browser never keeps an old version."""
    digest = hashlib.sha256((HERE / "static" / name).read_bytes()).hexdigest()[:12]
    return f"/static/{name}?v={digest}"


MCP_PATH = "/mcp"
PAGE_SIZE = 50
MATCH_LIMIT = 20
MCP_DOCS_PATH = "/mcp/docs"
# The [web] settings that snapdelta-web reads once, at its start.
START_KEYS = ("host", "port", "auth", "token", "tls_cert", "tls_key", "allowed_hosts")
# Any other Host header means a DNS rebinding page tries to read the store through the browser.
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
SIDES = ("left", "right")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
SAME_SITE = ("same-origin", "none")


def create_app(state: AppState) -> FastAPI:
    """The web app; `state` holds the settings and the store, which a save of the settings reloads."""
    configure_logging(state.settings.snapshot.log_level)
    mcp = create_mcp(state)
    # The host and the token checks of this app protect /mcp, so the SDK's own host check stays off.
    mcp_routes = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    ).routes

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            state.close()

    app = FastAPI(title="snapdelta", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.router.routes.extend(mcp_routes)
    # Fixed at the start: a save of the settings must not open a server that runs on the network.
    backend = auth_backend(state.settings)
    session_key = backend.session_key()
    access = backend.describe()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts(state.settings, backend))

    @app.middleware("http")
    async def same_site_only(request: Request, call_next: Any) -> Response:
        """A POST from another site open in the browser must not delete or write anything."""
        if request.method not in SAFE_METHODS:
            fetch_site = request.headers.get("sec-fetch-site")
            origin = request.headers.get("origin")
            own_host = urlsplit(f"//{request.headers.get('host', '')}").hostname
            foreign_origin = origin is not None and urlsplit(origin).hostname != own_host
            if (fetch_site and fetch_site not in SAME_SITE) or foreign_origin:
                return PlainTextResponse("cross-site request refused", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def require_user(request: Request, call_next: Any) -> Response:
        """When the backend asks for a user, a page needs a login, and the API needs what the backend reads."""
        path = request.url.path
        user = session_user(session_key, request.cookies.get(SESSION_COOKIE)) if backend.login_fields else None
        if user is None:
            user = backend.from_request(request.headers, request.client.host if request.client else None)
        if user is None and backend.access == Access.LOGIN and not path.startswith(OPEN_PATHS):
            if path.startswith("/api/") or path == MCP_PATH:
                message = {"error": backend.api_hint}
                return JSONResponse(message, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            if request.method in SAFE_METHODS and backend.login_fields:
                wanted = f"{path}?{request.url.query}" if request.url.query else path
                return RedirectResponse(f"/login?{urlencode({'next': wanted})}", status_code=303)
            return PlainTextResponse("log in first" if backend.login_fields else backend.api_hint, status_code=401)
        request.state.user = user
        # The runs of this request record the user, from the threads of the request too.
        acting = acting_user.set(user.name if user else None)
        try:
            return await call_next(request)
        finally:
            acting_user.reset(acting)

    @app.middleware("http")
    async def mcp_page_for_browsers(request: Request, call_next: Any) -> Response:
        """A browser that opens /mcp gets the page of the server; an MCP client gets the protocol."""
        browser = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        if request.url.path == MCP_PATH and browser:
            return RedirectResponse(MCP_DOCS_PATH, status_code=303)
        return await call_next(request)

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["when"] = views.when
    templates.env.filters["size"] = views.size_text
    templates.env.filters["note_line"] = note_line
    templates.env.filters["ticks"] = views.ticks
    templates.env.filters["id_label"] = views.id_label
    templates.env.globals["asset"] = asset_url
    templates.env.globals["snapdelta_version"] = package_version("snapdelta")
    templates.env.globals["links"] = {
        "project": PROJECT_URL,
        "docs": DOCS_URL,
        "changelog": CHANGELOG_URL,
        "issues": ISSUES_URL,
    }
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def render(request: Request, template: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
        user: User | None = getattr(request.state, "user", None)
        signed_in = bool(backend.login_fields) and backend.access == Access.LOGIN
        context = {"signed_in": signed_in, "user_name": user.name if user else None} | context
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def login_context(next: str, failed: bool) -> dict[str, Any]:
        return {
            "tab": None,
            "next": safe_next(next),
            "failed": failed,
            "signed_in": False,
            "fields": backend.login_fields,
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """For a container or a load balancer: the server answers; it needs no login and reads no store."""
        return {"status": "ok"}

    @app.get("/login", response_model=None)
    def login_page(request: Request, next: str = "") -> HTMLResponse | RedirectResponse:
        if not backend.login_fields or backend.access != Access.LOGIN:
            return RedirectResponse(safe_next(next), status_code=303)
        return render(request, "login.html", login_context(next, failed=False))

    @app.post("/login", response_model=None)
    async def login(request: Request) -> HTMLResponse | RedirectResponse:
        form = {key: str(value) for key, value in (await request.form()).items()}
        next_path = form.get("next", "")
        if backend.access != Access.LOGIN:
            return RedirectResponse(safe_next(next_path), status_code=303)
        user = backend.login(form) if backend.login_fields else None
        if user is None:
            return render(request, "login.html", login_context(next_path, failed=True), 401)
        response = RedirectResponse(safe_next(next_path), status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session_value(session_key, user),
            max_age=SESSION_TTL_S,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @app.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    snapshot_cache = SnapshotCache()
    take_jobs = TakeJobs()
    # A saved output never changes, but a new one can take the name of a deleted one: a delete drops its row.
    row_cache: views.RowCache = LruCache(1024)
    # A step compares two saved snapshots, which never change: the same two give the same delta.
    step_cache: views.StepCache = LruCache(4096)
    app.mount(API_PREFIX, create_api(state, row_cache))

    def cards(output: Output, model: str, filters: views.CardFilters, offset: int) -> dict[str, Any]:
        built: list[views.Card] = []
        # The snapshots only add the unchanged values of the cards: a model without a difference needs neither.
        if output.diff.get(model):
            pairs = output.profile.rules.pairs if output.profile else None
            left = snapshot_cache.side(state.store, output.left)
            right = snapshot_cache.side(state.store, output.right, pairs)
            built = views.build_cards(output, model, left, right)
        page = views.card_page(built, filters.statuses, filters.query, offset, PAGE_SIZE)
        return {"page": page, "left": output.left, "right": output.right, "model": model, "filters": filters}

    def check_side(side: str) -> None:
        if side not in SIDES:
            raise NotFoundError(f"no side {side!r}: left or right")

    def snapshot_options(target: str, side: str, offset: int, selected: str | None) -> dict[str, Any]:
        entries = state.store.snapshot_entries(SnapshotQuery(target=target))
        start = max(offset, 0)
        return {
            "target": target,
            "side": side,
            "offset": start,
            "next_offset": start + PAGE_SIZE,
            "entries": entries[start : start + PAGE_SIZE],
            "more": len(entries) > start + PAGE_SIZE,
            "selected": selected or f"{target}@{LATEST}",
        }

    @app.exception_handler(SnapdeltaError)
    async def snapdelta_error(request: Request, error: SnapdeltaError) -> HTMLResponse:
        if isinstance(error, NotFoundError):
            return not_found(request, str(error))
        return render(request, "error.html", {"tab": None, "message": str(error)}, 400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        if error.status_code == 404:
            return not_found(request, None)
        return PlainTextResponse(str(error.detail), status_code=error.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> HTMLResponse:
        # The details stay in the server log: they can hold paths and data of the state.store.
        return render(request, "error.html", {"tab": None, "message": None}, 500)

    def not_found(request: Request, message: str | None) -> HTMLResponse:
        return render(request, "not_found.html", {"tab": None, "path": request.url.path, "message": message}, 404)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> RedirectResponse:
        return RedirectResponse("/static/favicon.svg", status_code=301)

    @app.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/diffs")

    @app.get("/diffs", response_class=HTMLResponse)
    def diffs(
        request: Request, q: str = "", ticket: str = "", page: int = 1, deleted: int | None = None
    ) -> HTMLResponse:
        wanted = normalize_ticket(ticket)
        rows, unreadable, pager = views.output_rows(state.store, q, page, PAGE_SIZE, row_cache, wanted)
        context = {"tab": "diffs", "rows": rows, "unreadable": unreadable, "pager": pager, "q": q, "deleted": deleted}
        context["ticket"] = wanted
        context["groups"] = views.output_groups(rows)
        return render(request, "diffs.html", context)

    @app.get("/diffs/{name}", response_class=HTMLResponse)
    def diff(
        request: Request,
        name: str,
        model: str | None = None,
        status: Annotated[list[str] | None, Query()] = None,
        filtered: bool = False,
        q: str = "",
        offset: int = 0,
    ) -> HTMLResponse:
        """The whole page, or only the cards for the htmx requests of the filters, the models and "Show more"."""
        output = state.store.load_output(state.store.resolve_output(name))
        deltas = views.model_deltas(output)
        if model not in output.compared:
            model = next((candidate for candidate, delta in deltas if delta.total), output.compared[0])
        filters = views.CardFilters.parse(status, filtered, q)
        model_delta = dict(deltas)[model]
        context = {"name": name, "model_delta": model_delta} | cards(output, model, filters, offset)
        if _cards_only(request):
            # A new model changes the counts of the chips, which sit outside the cards.
            return render(request, "_cards.html", context | {"chip_counts": offset == 0})
        delta = views.output_delta(output)
        context |= {"tab": "diffs", "output": output, "delta": delta, "deltas": deltas, "formats": list(renderers())}
        context["reviewed"] = output.review_counts()
        return render(request, "diff.html", context)

    @app.get("/diffs/{name}/export/{output_format}")
    def export_diff(name: str, output_format: str) -> Response:
        resolved = state.store.resolve_output(name)
        chosen = renderer(output_format)
        headers = views.attachment(f"{resolved}.{chosen.suffix}")
        return Response(chosen.render(state.store.load_output(resolved)), media_type=chosen.media_type, headers=headers)

    @app.post("/diffs")
    def run_diff(
        left: Annotated[str | None, Form()] = None,
        right: Annotated[str | None, Form()] = None,
        models: Annotated[list[str] | None, Form()] = None,
        pick_models: Annotated[bool, Form()] = False,
        profile: Annotated[str, Form()] = "",
        ticket: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        if not left or not right:
            raise SnapdeltaError("select a left and a right snapshot")
        if pick_models and not models:
            raise SnapdeltaError("select at least one model to compare")
        done = diff_snapshots(
            state.store,
            state.settings,
            left,
            right,
            models=models,
            profile=profile or None,
            ticket=ticket,
            note=note,
            origin="web",
        )
        return RedirectResponse(f"/diffs/{done.name}", status_code=303)

    @app.post("/diffs/targets", response_class=HTMLResponse)
    def run_target_diffs(
        request: Request,
        left: Annotated[str, Form()],
        right: Annotated[str, Form()] = "",
        group: Annotated[str, Form()] = "",
        live: Annotated[bool, Form()] = False,
        profile: Annotated[str, Form()] = "",
        ticket: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ) -> HTMLResponse:
        """For each host of the group, or each target of the store, TARGET@LEFT with TARGET@RIGHT or the target now."""
        results = diff_targets(
            state.store,
            state.settings,
            left.strip(),
            None if live else right.strip() or None,
            groups=[group] if group else [],
            all_targets=not group,
            live=live,
            profile=profile or None,
            ticket=ticket,
            note=note,
            origin="web",
        )
        items = list(results.values())
        context = {
            "tab": "new",
            "results": items,
            "left": left.strip(),
            "right": "now" if live else right.strip(),
            "ticket": normalize_ticket(ticket),
            "changed": [item for item in items if item.changed],
            "unchanged": [item for item in items if item.output is not None and not item.changed],
            "failed": [item for item in items if item.error],
        }
        return render(request, "diffs_targets.html", context)

    @app.post("/diffs/{name}/describe")
    def describe_diff(
        name: str,
        ticket: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        resolved = state.store.resolve_output(name)
        state.store.describe_output(resolved, ticket=ticket, note=note)
        row_cache.discard((state.store.location, resolved))
        return RedirectResponse(f"/diffs/{resolved}", status_code=303)

    @app.post("/diffs/{name}/reviews", response_model=None)
    def review_change(
        request: Request,
        name: str,
        key: Annotated[str, Form()],
        by: Annotated[str, Form()] = "",
        comment: Annotated[str, Form()] = "",
        undo: Annotated[bool, Form()] = False,
    ) -> HTMLResponse | RedirectResponse:
        """Accept one change, or withdraw the acceptance; htmx gets the new review bar and the new count."""
        resolved = state.store.resolve_output(name)
        output = state.store.review_output(resolved, key, None if undo else new_review(by, comment))
        row_cache.discard((state.store.location, resolved))
        if "hx-request" not in request.headers:
            return RedirectResponse(f"/diffs/{resolved}", status_code=303)
        context = {"name": resolved, "key": key, "review": output.reviews.get(key), "reviewed": output.review_counts()}
        return render(request, "_review.html", context)

    @app.post("/diffs/live")
    def run_live_diff(left: Annotated[str, Form()], profile: Annotated[str, Form()] = "") -> RedirectResponse:
        """The snapshot against its target now: a new snapshot of the target, then the compare."""
        done = diff_snapshots(state.store, state.settings, left, live=True, profile=profile or None, origin="web")
        return RedirectResponse(f"/diffs/{done.name}", status_code=303)

    @app.get("/new/models", response_class=HTMLResponse)
    def new_diff_models(request: Request, left: str = "", right: str = "") -> HTMLResponse:
        if not left or not right:
            return HTMLResponse("")
        # The file is enough to know the models: no diffsync load, which is slow for big snapshots.
        left_file = state.store.read_snapshot(state.store.resolve_snapshot(left))
        right_file = state.store.read_snapshot(state.store.resolve_snapshot(right))
        left_models, right_models = top_level_models(left_file), top_level_models(right_file)
        context = {
            "left": left_file,
            "right": right_file,
            "common": common_models(left_models, right_models),
            "skipped": skipped_models(left_models, right_models),
        }
        return render(request, "_models.html", context)

    @app.get("/snapshots", response_class=HTMLResponse)
    def snapshots(
        request: Request,
        target: str = "",
        label: str = "",
        ticket: str = "",
        page: int = 1,
        deleted: int | None = None,
    ) -> HTMLResponse:
        query = views.snapshot_query(target, label, ticket)
        rows, pager = views.snapshot_list(state.store, query, page, PAGE_SIZE)
        context = {"tab": "snapshots", "rows": rows, "pager": pager, "query": query, "deleted": deleted}
        context |= {"filters": views.snapshot_filters(query), "targets": state.store.targets()}
        return render(request, "snapshots.html", context)

    @app.get("/snapshots/{target}", response_class=HTMLResponse)
    def target_page(
        request: Request, target: str, page: int = 1, deleted: int | None = None, since: str = "", until: str = ""
    ) -> HTMLResponse:
        period = views.Period.parse(since, until)
        rows, unreadable, pager = views.target_snapshot_rows(state.store, target, page, PAGE_SIZE, period)
        if not rows and not unreadable and page == 1 and not period.active:
            raise NotFoundError(f"no snapshot of {target}")
        context = {"tab": "snapshots", "target": target, "rows": rows, "unreadable": unreadable, "pager": pager}
        context |= {"deleted": deleted, "period": period}
        return render(request, "target.html", context)

    @app.get("/targets/{target}/timeline", response_class=HTMLResponse)
    def target_timeline(
        request: Request, target: str, profile: str = "", since: str = "", until: str = ""
    ) -> HTMLResponse:
        """Each snapshot of the target, and the size of the delta from the one before."""
        rules = state.settings.profile(profile or None)
        period = views.Period.parse(since, until)
        built = views.timeline(state.store, target, rules, profile or None, step_cache, period=period)
        context = {"timeline": built, "target": target, "profiles": profile_choices(), "profile": profile}
        context["period"] = period
        return render(request, "_timeline.html", context)

    @app.get("/snapshots/{target}/{name}", response_class=HTMLResponse)
    def snapshot(request: Request, target: str, name: str, model: str | None = None, offset: int = 0) -> HTMLResponse:
        """The whole page, or the next rows of one model for the htmx request of "Show more"."""
        reference = state.store.resolve_snapshot(f"{target}/{name}")
        adapter = snapshot_cache.load(state.store, reference)
        size = PAGE_SIZE
        context: dict[str, Any] = {"reference": reference}
        if model is not None:
            if model not in adapter.loaded:
                raise NotFoundError(f"model {model} is not loaded in {reference}")
            return render(
                request, "_model_rows.html", context | {"table": views.model_table(adapter, model, offset, size)}
            )
        live = getattr(source_classes().get(adapter.source), "fetches", False)
        context |= {"tab": "snapshots", "adapter": adapter, "tables": views.model_tables(adapter, size), "live": live}
        context["profiles"] = profile_choices()
        return render(request, "snapshot.html", context)

    @app.get("/snapshots/{target}/{name}/export/json")
    def export_snapshot(target: str, name: str) -> Response:
        reference = state.store.resolve_snapshot(f"{target}/{name}")
        headers = views.attachment(f"{reference.replace('/', '-')}.json")
        body = state.store.read_snapshot(reference).model_dump_json(by_alias=True, indent=2)
        return Response(body, media_type="application/json", headers=headers)

    def group_members(group: str) -> list[str] | None:
        inventory = state.settings.hosts()
        return inventory.members(group) if group and inventory else None

    @app.get("/activity", response_class=HTMLResponse)
    def activity(
        request: Request, kind: str = "", status: str = "", since: str = "", until: str = "", page: int = 1
    ) -> HTMLResponse:
        """The newest runs: snapshots and diffs, from the CLI, the web UI, the API and MCP."""
        size = PAGE_SIZE
        period = views.Period.parse(since, until)
        filtered = bool(status) or period.active
        found = state.store.runs(limit=ACTIVITY_WINDOW if filtered else size * page + 1, kind=kind or None)
        if filtered:
            found = [run for run in found if (not status or run.status == status) and period.contains(run.started_at)]
        runs = found[size * (page - 1) : size * page]
        chosen = (("status", status), ("since", period.since_text), ("until", period.until_text))
        context = {"tab": "activity", "runs": runs, "kind": kind, "page": page, "more": len(found) > size * page}
        context |= {"status": status, "period": period, "filtered": filtered}
        context["filters"] = urlencode([(name, value) for name, value in chosen if value])
        return render(request, "activity.html", context)

    @app.get("/activity/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str, level: str = "debug") -> HTMLResponse:
        run = state.store.load_run(run_id)
        lines = views.log_at_level(run.log, level)
        context = {"tab": "activity", "run": run, "lines": lines, "level": level, "levels": views.LOG_LEVELS}
        return render(request, "run.html", context)

    @app.get(MCP_DOCS_PATH, response_class=HTMLResponse)
    async def mcp_docs(request: Request) -> HTMLResponse:
        """The tools, resources and prompts of the MCP server, read from it, with a form to try each tool."""
        tools = [
            (tool, views.tool_parameters(tool.input_schema), views.example_arguments(tool.input_schema))
            for tool in await mcp.list_tools()
        ]
        context = {
            "tab": None,
            "tools": tools,
            "resources": await mcp.list_resources(),
            "prompts": await mcp.list_prompts(),
            "guide": MCP_GUIDE,
            "endpoint": f"{str(request.base_url).rstrip('/')}{MCP_PATH}",
            "version": package_version("snapdelta-web"),
        }
        return render(request, "mcp_docs.html", context)

    @app.post(MCP_DOCS_PATH + "/try/{tool}", response_class=HTMLResponse)
    async def mcp_try(request: Request, tool: str, arguments: Annotated[str, Form()] = "{}") -> HTMLResponse:
        """Call one tool as an agent would, and show its answer."""
        try:
            values = json.loads(arguments or "{}")
            if not isinstance(values, dict):
                raise ToolError("give the arguments as a JSON object")
            result = await mcp.call_tool(tool, values)
            structured = getattr(result, "structured_content", None)
            texts = [getattr(item, "text", "") for item in getattr(result, "content", [])]
            answer = json.dumps(structured, indent=2) if structured is not None else "\n".join(texts)
            failed = bool(getattr(result, "is_error", False))
        except json.JSONDecodeError as error:
            answer, failed = f"not JSON: {error}", True
        except ToolError as error:
            answer, failed = str(error), True
        return render(request, "_mcp_result.html", {"answer": answer, "failed": failed})

    @app.get("/palette", response_class=HTMLResponse)
    def palette(request: Request, q: str = "") -> HTMLResponse:
        """The results of the command palette (Ctrl+K)."""
        context = {"groups": views.palette(state.store, q, row_cache), "query": q}
        return render(request, "_palette.html", context)

    @app.get("/targets", response_class=HTMLResponse)
    def target_options(request: Request, side: str, q: str = "", group: str = "") -> HTMLResponse:
        check_side(side)
        matches, more = views.target_matches(state.store, q, MATCH_LIMIT, group_members(group))
        return render(request, "_target_options.html", {"side": side, "targets": matches, "more": more})

    @app.get("/targets/{target}/snapshots", response_class=HTMLResponse)
    def target_snapshots(request: Request, target: str, side: str, offset: int = 0) -> HTMLResponse:
        check_side(side)
        return render(request, "_snapshot_options.html", snapshot_options(target, side, offset, None))

    def profile_choices() -> list[tuple[str, str]]:
        """The profiles by name, each with the text of its option: the name and its description."""
        return [
            (name, f"{name}: {profile.description}" if profile.description else name)
            for name, profile in sorted(state.settings.profiles.items())
        ]

    @app.get("/new", response_class=HTMLResponse)
    def new(
        request: Request,
        left: str | None = None,
        right: str | None = None,
        ticket: str = "",
        note: str = "",
        profile: str = "",
        many: bool = False,
    ) -> HTMLResponse:
        pickers = {}
        missing = []
        ticket_text = normalize_ticket(ticket)
        in_ticket = f" in the ticket {ticket_text}" if ticket_text else ""
        for side, selected in (("left", left), ("right", right)):
            if selected and "@" in selected and not selected.endswith(f"@{LATEST}"):
                # TARGET@LABEL selects the snapshot that it names, as one of the choices of the list.
                target, _, label = selected.partition("@")
                try:
                    selected = state.store.resolve_snapshot(selected, ticket_text)
                except NotFoundError:
                    missing.append(f"No snapshot of {target} is labelled {label}{in_ticket}")
                    selected = f"{target}@{LATEST}"
            target = selected.split("@")[0].split("/")[0] if selected else None
            options = snapshot_options(target, side, 0, selected) if target else None
            pickers[side] = {
                "targets": views.target_matches(state.store, "", MATCH_LIMIT)[0],
                "options": options,
            }
        inventory = state.settings.hosts()
        groups = inventory.groups if inventory else []
        members = {group: inventory.members(group) for group in groups} if inventory else {}
        context = {"tab": "new", "pickers": pickers, "profiles": profile_choices(), "groups": groups, "many": many}
        context |= {
            "missing": missing,
            "ticket": ticket,
            "note": note,
            "profile": profile,
        }
        context |= {"group_members": members}
        return render(request, "new.html", context)

    @app.post("/diffs/delete")
    def delete_diffs(
        names: Annotated[list[str] | None, Form()] = None,
        q: Annotated[str, Form()] = "",
        ticket: Annotated[str, Form()] = "",
        page: Annotated[int, Form()] = 1,
    ) -> RedirectResponse:
        resolved = [state.store.resolve_output(name) for name in _selected(names)]
        for name in resolved:
            state.store.delete_output(name)
            row_cache.discard((state.store.location, name))
        query = urlencode(
            {key: value for key, value in (("q", q), ("ticket", ticket), ("page", page)) if value}
            | {"deleted": len(resolved)}
        )
        return RedirectResponse(f"/diffs?{query}", status_code=303)

    @app.post("/snapshots/{target}/compare")
    def compare_snapshots(target: str, references: Annotated[list[str] | None, Form()] = None) -> RedirectResponse:
        """The diff of the two selected snapshots: the older one is the left side."""
        resolved = [state.store.resolve_snapshot(reference) for reference in _selected(references)]
        if len(resolved) != 2:
            raise SnapdeltaError(f"select two snapshots to compare, not {len(resolved)}")
        newest_first = [entry.reference for entry in state.store.snapshot_entries(SnapshotQuery(target=target))]
        if any(reference not in newest_first for reference in resolved):
            raise NotFoundError(f"a selected snapshot is not a snapshot of {target}")
        newer, older = sorted(resolved, key=newest_first.index)
        done = diff_snapshots(state.store, state.settings, older, newer, origin="web")
        return RedirectResponse(f"/diffs/{done.name}", status_code=303)

    @app.post("/snapshots/{target}/delete")
    def delete_snapshots(
        target: str,
        references: Annotated[list[str] | None, Form()] = None,
        page: Annotated[int, Form()] = 1,
        since: Annotated[str, Form()] = "",
        until: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        resolved = [state.store.resolve_snapshot(reference) for reference in _selected(references)]
        if any(reference.partition("/")[0] != target for reference in resolved):
            raise NotFoundError(f"a selected snapshot is not a snapshot of {target}")
        for reference in resolved:
            state.store.delete_snapshot(reference)
        if not state.store.snapshot_entries(SnapshotQuery(target=target)):
            return RedirectResponse(f"/snapshots?deleted={len(resolved)}", status_code=303)
        period = views.Period.parse(since, until)
        kept = f"{period.query}&" if period.active else ""
        return RedirectResponse(f"/snapshots/{target}?{kept}page={page}&deleted={len(resolved)}", status_code=303)

    @app.post("/diffs/{name}/delete")
    def delete_diff(name: str) -> RedirectResponse:
        resolved = state.store.resolve_output(name)
        state.store.delete_output(resolved)
        row_cache.discard((state.store.location, resolved))
        return RedirectResponse("/diffs", status_code=303)

    @app.post("/snapshots/{target}/{name}/delete")
    def delete_snapshot(target: str, name: str) -> RedirectResponse:
        state.store.delete_snapshot(state.store.resolve_snapshot(f"{target}/{name}"))
        target = f"/snapshots/{target}" if state.store.snapshot_entries(SnapshotQuery(target=target)) else "/snapshots"
        return RedirectResponse(target, status_code=303)

    def collectors() -> dict[str, type[Source]]:
        return {name: cls for name, cls in sorted(source_classes().items()) if cls.fetches}

    def collector(source: str) -> type[Source]:
        found = collectors()
        if source not in found:
            names = ", ".join(found) or "none"
            raise SnapdeltaError(f"source {source!r} does not fetch its data (fetching sources: {names})")
        return found[source]

    def known_targets(source: str) -> list[str]:
        """The targets that the pickers offer: the hosts of the inventory by platform, or the list of the source."""
        if source == BY_PLATFORM:
            inventory = state.settings.hosts()
            return sorted(inventory.hosts) if inventory else []
        cls = collector(source)
        return cls.targets(state.settings.source(cls))

    @app.get("/take", response_class=HTMLResponse)
    def take_form(request: Request) -> HTMLResponse:
        inventory = state.settings.hosts()
        context = {
            "tab": "snapshots",
            "sources": list(collectors()),
            "by_platform": inventory is not None,
            "groups": inventory.groups if inventory else [],
        }
        return render(request, "take.html", context)

    @app.get("/take/targets", response_class=HTMLResponse)
    def take_targets(
        request: Request, source: str, q: str = "", target: Annotated[list[str] | None, Query()] = None
    ) -> HTMLResponse:
        wanted = q.strip()
        selected = set(target or [])
        known = known_targets(source)
        matches = [name for name in known if wanted.lower() in name.lower() and name not in selected]
        typed = wanted if wanted and not matches and wanted not in known and wanted not in selected else None
        more = max(len(matches) - MATCH_LIMIT, 0)
        context = {"targets": matches[:MATCH_LIMIT], "more": more, "typed": typed}
        return render(request, "_take_targets.html", context)

    @app.post("/take", response_model=None)
    def take(
        request: Request,
        source: Annotated[str, Form()] = BY_PLATFORM,
        target: Annotated[list[str] | None, Form()] = None,
        group: Annotated[list[str] | None, Form()] = None,
        all_targets: Annotated[bool, Form()] = False,
        label: Annotated[str, Form()] = "",
        ticket: Annotated[str, Form()] = "",
    ) -> HTMLResponse | RedirectResponse:
        cls = None if source == BY_PLATFORM else collector(source)
        names = target_names(cls, target or [], all_targets, state.settings, group or [])
        if not names:
            raise SnapdeltaError("select a target, a group, or all targets")
        plan = plan_sources(names, state.settings, cls)
        # The loads run in the background: the page of the job follows each target live.
        job = take_jobs.start(state, plan, source, label.strip() or None, normalize_ticket(ticket))
        return RedirectResponse(f"/take/jobs/{job.id}", status_code=303)

    @app.get("/take/jobs/{job_id}", response_class=HTMLResponse)
    def take_job(request: Request, job_id: str) -> HTMLResponse:
        """The job page; htmx asks for its rows every second until the job ends."""
        context = {"tab": "snapshots", "job": take_jobs.get(job_id)}
        if _cards_only(request):
            return render(request, "_take_job.html", context)
        return render(request, "take_job.html", context)

    @app.post("/take/jobs/{job_id}/retry")
    def retry_take_job(job_id: str) -> RedirectResponse:
        """A new job with the failed targets of this one, with the same source, label and ticket."""
        job = take_jobs.get(job_id)
        if not job.failed:
            raise SnapdeltaError("no failed target to take again")
        cls = None if job.source == BY_PLATFORM else collector(job.source)
        retry = take_jobs.start(state, plan_sources(job.failed, state.settings, cls), job.source, job.label, job.ticket)
        return RedirectResponse(f"/take/jobs/{retry.id}", status_code=303)

    @app.get("/admin/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: str | None = None, restart: bool = False) -> HTMLResponse:
        return render(request, "admin_settings.html", settings_context(SETTINGS, saved=saved, restart=restart))

    @app.post("/admin/settings", response_model=None)
    async def save_settings_page(request: Request) -> HTMLResponse | RedirectResponse:
        return await save_section(request, SETTINGS)

    @app.get("/admin/inventory", response_class=HTMLResponse)
    def inventory_page(request: Request) -> HTMLResponse:
        inventory = state.settings.hosts()
        context = {
            "tab": "admin",
            "section": "inventory",
            "inventory": inventory,
            "sources": {name: platform_source(state.settings, host.platform) for name, host in inventory.hosts.items()}
            if inventory
            else {},
        }
        return render(request, "admin_inventory.html", context)

    @app.get("/admin/about", response_class=HTMLResponse)
    def about_page(request: Request) -> HTMLResponse:
        inventory = state.settings.inventory.location
        context = {
            "tab": "admin",
            "section": "about",
            "components": components(["snapdelta-web"]),
            "plugins": plugins(),
            "info": state.store.info(),
            "settings_file": state.settings.file,
            "inventory": inventory,
            "access": access,
        }
        return render(request, "admin_about.html", context)

    @app.get("/admin/models", response_class=HTMLResponse)
    def models_page(request: Request) -> HTMLResponse:
        context = {"tab": "admin", "section": "models", "sets": catalog(source_classes()), "broken": broken_sources()}
        return render(request, "admin_models.html", context)

    @app.get("/admin/plugins", response_class=HTMLResponse)
    def plugins_page(request: Request) -> HTMLResponse:
        reports = plugin_reports(settings=state.settings)
        points = [(point, [item for item in reports if item.group == point.group]) for point in extension_points()]
        return render(request, "admin_plugins.html", {"tab": "admin", "section": "plugins", "points": points})

    @app.get("/admin/profiles", response_class=HTMLResponse)
    def profiles_page(request: Request, saved: str | None = None) -> HTMLResponse:
        return render(request, "admin_settings.html", settings_context(PROFILES, saved=saved))

    @app.post("/admin/profiles", response_model=None)
    async def save_profiles_page(request: Request) -> HTMLResponse | RedirectResponse:
        return await save_section(request, PROFILES)

    def section_groups(section: str) -> list[FormGroup]:
        if section == PROFILES:
            return profile_groups(state.settings)
        plugin_choices = {
            "snapshot.runner": sorted(discover(RUNNERS).plugins),
            "web.auth": sorted(discover(AUTH).plugins),
            "inventory.format": sorted(discover(INVENTORIES).plugins),
        }
        return form_groups(state.settings, source_classes(), plugin_choices)

    async def save_section(request: Request, section: str) -> HTMLResponse | RedirectResponse:
        """Save the tables of one Admin tab; the other tables and the comments of the file stay."""
        installed = source_classes()
        groups = section_groups(section)
        posted = {key: str(value) for key, value in (await request.form()).items()}
        path = state.settings.file or user_settings_path()
        previous = path.read_bytes() if path.is_file() else None
        try:
            parsed = parse_form(posted, groups)
            save_settings(path, parsed.values, installed, parsed.removed)
        except SettingsError as error:
            errors = errors_by_field(error.errors, groups, posted.get(NEW_PROFILE_NAME, "").strip())
            context = settings_context(section, errors=errors, posted=posted)
            return render(request, "admin_settings.html", context, 400)
        except OSError as error:
            context = settings_context(section, failure=f"{path}: {error.strerror}", posted=posted)
            return render(request, "admin_settings.html", context, 400)
        started = [getattr(state.settings.web, key) for key in START_KEYS]
        try:
            state.reload()
            configure_logging(state.settings.snapshot.log_level)
        except (OSError, SnapdeltaError) as error:
            # The running state did not change, so the file goes back to match it.
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
            failure = f"the store or the settings cannot be opened: {error}"
            context = settings_context(section, failure=failure, posted=posted)
            return render(request, "admin_settings.html", context, 400)
        changed = started != [getattr(state.settings.web, key) for key in START_KEYS]
        restart = "&restart=true" if changed else ""
        return RedirectResponse(f"/admin/{section}?{urlencode({'saved': str(path)})}{restart}", status_code=303)

    def settings_context(section: str, **extra: Any) -> dict[str, Any]:
        return {
            "tab": "admin",
            "section": section,
            "groups": section_groups(section),
            "file": state.settings.file,
            "target_file": state.settings.file or user_settings_path(),
            "warnings": state.settings.warnings,
            "errors": {},
            "posted": None,
            "failure": None,
            "saved": None,
            "restart": False,
            "secret_backends": sorted(discover(SECRETS).plugins),
        } | extra

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, deleted: str | None = None) -> HTMLResponse:
        context = {"tab": "admin", "info": state.store.info(), "plan": None, "form": {}, "deleted": deleted}
        return render(request, "admin.html", context)

    @app.post("/admin/prune", response_class=HTMLResponse)
    def prune(
        request: Request,
        target: Annotated[str, Form()] = "",
        keep: Annotated[str, Form()] = "",
        older_than: Annotated[str, Form()] = "",
        outputs: Annotated[str, Form()] = "",
        confirm: Annotated[str, Form()] = "",
    ) -> Response:
        plan = _prune_plan(state.store, target, keep, older_than, bool(outputs))
        if confirm:
            state.store.apply(plan)
            return RedirectResponse(f"/admin?deleted={plan.counts}", status_code=303)
        form = {"target": target, "keep": keep, "older_than": older_than, "outputs": outputs}
        context = {"tab": "admin", "info": state.store.info(), "plan": plan, "form": form, "deleted": None}
        return render(request, "admin.html", context)

    return app


def _allowed_hosts(settings: Settings, backend: AuthBackend) -> list[str]:
    """The Host headers to accept; without a login, any other name means a DNS rebinding page reads the store."""
    if backend.access == Access.OPEN:
        return [*LOCAL_HOSTS, *settings.web.allowed_hosts]
    if backend.access == Access.LOCAL:
        return LOCAL_HOSTS
    return [*LOCAL_HOSTS, *settings.web.allowed_hosts] if settings.web.allowed_hosts else ["*"]


def _cards_only(request: Request) -> bool:
    # On "back", htmx asks for the whole page again when its history cache misses.
    return "hx-request" in request.headers and "hx-history-restore-request" not in request.headers


def _selected(items: list[str] | None) -> list[str]:
    if not items:
        raise SnapdeltaError("select at least one item to delete")
    return items


def _prune_plan(store: Store, target: str, keep: str, older_than: str, outputs: bool) -> PrunePlan:
    try:
        kept = int(keep) if keep.strip() else None
        days = int(older_than) if older_than.strip() else None
    except ValueError as error:
        raise SnapdeltaError("keep and age must be whole numbers") from error
    cutoff = age_cutoff(days) if days is not None else None
    return store.prune_plan(target=target.strip() or None, keep=kept, older_than=cutoff, outputs=outputs)
