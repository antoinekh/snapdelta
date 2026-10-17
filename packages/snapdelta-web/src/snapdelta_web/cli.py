"""snapdelta-web: serve the web UI and the API of the store."""

from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from diffsync.logging import enable_console_logging

from snapdelta.cli import SettingsOption, StoreOption
from snapdelta.errors import SnapdeltaError
from snapdelta.settings import load_settings
from snapdelta.store import open_store_from

from .api import PREFIX as API_PREFIX
from .app import create_app
from .auth import Access, auth_backend
from .state import AppState

# Without a login, the server listens on the loopback interface only, unless its backend is open by choice.
LOOPBACK = ("127.0.0.1", "localhost", "::1")

app = typer.Typer(add_completion=False)


@app.command()
def main(
    store: StoreOption = None,
    settings_file: SettingsOption = None,
    host: Annotated[
        str | None,
        typer.Option(help="Address to listen on; default from the settings (127.0.0.1); else a token, or auth none."),
    ] = None,
    port: Annotated[int | None, typer.Option(help="Port; default from the settings (8765).")] = None,
    tls_cert: Annotated[Path | None, typer.Option(help="Certificate file for HTTPS, with --tls-key.")] = None,
    tls_key: Annotated[Path | None, typer.Option(help="Private key file for HTTPS, with --tls-cert.")] = None,
) -> None:
    """Serve the snapdelta web UI and its API; another address than 127.0.0.1 needs a token, or auth none."""
    enable_console_logging(verbosity=0)
    try:
        settings = load_settings(settings_file, store)
        backend = auth_backend(settings)
    except SnapdeltaError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    for warning in settings.warnings:
        typer.echo(f"warning: {warning}", err=True)
    listen = host or settings.web.host
    # The settings file already expands its own paths.
    cert = tls_cert.expanduser() if tls_cert else settings.web.tls_cert
    key = tls_key.expanduser() if tls_key else settings.web.tls_key
    open_access = backend.access == Access.OPEN
    if listen not in LOOPBACK and backend.access == Access.LOCAL:
        message = f"set [web] token, or another [web] auth, to listen on {listen}: the network needs a login"
        typer.echo(f"error: {message}", err=True)
        raise typer.Exit(2)
    if listen not in LOOPBACK and open_access and not settings.web.allowed_hosts:
        message = 'with auth none, list the host names of this server in [web] allowed_hosts ("*": any)'
        typer.echo(f"error: {message}", err=True)
        raise typer.Exit(2)
    if open_access:
        typer.echo("warning: auth none: anyone who reaches this server can read and change the store", err=True)
        if settings.web.token is not None:
            typer.echo("warning: auth none: [web] token is ignored", err=True)
    if (cert is None) != (key is None):
        typer.echo("error: give both the TLS certificate and its key", err=True)
        raise typer.Exit(2)
    if listen not in LOOPBACK and cert is None and backend.access == Access.LOGIN and backend.login_fields:
        typer.echo("warning: no TLS: the login travels in clear; use --tls-cert and --tls-key, or a proxy", err=True)
    state = AppState(settings, open_store_from(settings), settings_file, store)
    web = create_app(state)
    port = port or settings.web.port
    scheme = "https" if cert else "http"
    shown = "127.0.0.1" if listen in ("0.0.0.0", "::") else listen
    url = f"{scheme}://{shown}:{port}/diffs"
    typer.echo(f"snapdelta web UI: {url}; API: {scheme}://{shown}:{port}{API_PREFIX}/docs")
    uvicorn.run(
        web,
        host=listen,
        port=port,
        log_level="warning",
        ssl_certfile=str(cert) if cert else None,
        ssl_keyfile=str(key) if key else None,
    )
