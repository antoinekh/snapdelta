"""Who may use the web UI, the API and MCP: an auth backend, chosen by `[web] auth`, with its `[auth.NAME]` table.

The built-in backends are `token` (one shared token: a login for the pages, a Bearer token for the API), `header`
(the user that a trusted reverse proxy gives, for SSO) and `none`; a plugin adds others, such as LDAP or OIDC, in
the entry point group `snapdelta.auth`. After a login, the pages keep a signed session cookie with the user name.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from pydantic import Field

from snapdelta.errors import SnapdeltaError
from snapdelta.plugins import ExtensionPoint, Problem, Severity, require, settings_problems, subclass_check
from snapdelta.settings import Section, Settings, SettingsError, WebSettings

SESSION_COOKIE = "snapdelta_session"
SESSION_TTL_S = 12 * 3600
# The login page, the files that it needs and the health check; everything else needs a user when the backend asks.
OPEN_PATHS = ("/login", "/static/", "/favicon.ico", "/healthz")


class Access(StrEnum):
    """What a backend asks of a request."""

    # Every request needs a user: a login, a token, a header.
    LOGIN = "login"
    # No user is needed, so the server listens on this machine only.
    LOCAL = "local"
    # No user is needed on any address: for an isolated network only.
    OPEN = "open"


@dataclass(frozen=True)
class User:
    """Who sends a request; `name` is None for a user that the backend does not name, such as a shared token."""

    name: str | None = None


@dataclass(frozen=True)
class LoginField:
    name: str
    label: str
    secret: bool = False
    required: bool = True


class AuthSettings(Section):
    """The base of the settings of an auth backend; a plugin subclass adds its typed fields."""


class AuthBackend:
    """How a request proves who sends it: a login form for the pages, and what the request itself carries."""

    settings_class: ClassVar[type[AuthSettings]] = AuthSettings
    # The fields of the login page; none: the backend reads every request, and the server has no login page.
    login_fields: ClassVar[tuple[LoginField, ...]] = ()
    # What an API client without a user reads in the error.
    api_hint: ClassVar[str] = "this server needs a user"

    def __init__(self, web: WebSettings, settings: AuthSettings) -> None:
        self.web = web
        self.settings = settings
        self._session_key = secrets.token_bytes(32)

    @property
    def access(self) -> Access:
        return Access.LOGIN

    def describe(self) -> str:
        """One line for Admin > About: how this server lets users in."""
        return f"{type(self).__name__} login"

    def session_key(self) -> bytes:
        """The key of the session cookies: a new key ends every session; by default, a new one at each start."""
        return self._session_key

    def from_request(self, headers: Mapping[str, str], client: str | None) -> User | None:
        """The user that the request itself proves, such as a Bearer token or a header; None when it proves none."""
        return None

    def login(self, form: Mapping[str, str]) -> User | None:
        """The user of a login form, or None when the form does not prove one."""
        return None


class TokenSettings(AuthSettings):
    """The token itself is `[web] token`, so that the settings of a server before backends keep working."""


class TokenAuth(AuthBackend):
    """One token for everyone: a login of the pages, and the Bearer token of the API and MCP."""

    settings_class = TokenSettings
    api_hint = "give the token: Authorization: Bearer TOKEN"
    login_fields = (
        LoginField("token", "Token", secret=True),
        LoginField("name", "Your name", required=False),
    )

    def __init__(self, web: WebSettings, settings: AuthSettings) -> None:
        super().__init__(web, settings)
        self.token = web.token.value() if web.token is not None else None

    @property
    def access(self) -> Access:
        return Access.LOGIN if self.token is not None else Access.LOCAL

    def describe(self) -> str:
        return "Token login" if self.token is not None else "No login, this machine only"

    def session_key(self) -> bytes:
        # A new token gives a new key, so changing the token ends every session.
        return self.token.encode() if self.token is not None else super().session_key()

    def from_request(self, headers: Mapping[str, str], client: str | None) -> User | None:
        if self.token is not None and valid_bearer(self.token, headers.get("authorization")):
            return User()
        return None

    def login(self, form: Mapping[str, str]) -> User | None:
        if self.token is None or not valid_token(self.token, form.get("token", "")):
            return None
        return User(form.get("name", "").strip()[:80] or None)


class HeaderSettings(AuthSettings):
    header: str = Field(default="X-Forwarded-User", description="The header in which the proxy gives the user name.")
    trusted_proxies: list[str] = Field(
        default=["127.0.0.1", "::1"],
        description="The addresses or networks of the reverse proxies; a request from any other address is refused.",
        examples=["10.0.0.5\n10.0.1.0/24"],
    )


class HeaderAuth(AuthBackend):
    """The user that a trusted reverse proxy gives, after its own login (SSO, OIDC, LDAP, client certificates)."""

    settings_class = HeaderSettings
    api_hint = "the reverse proxy gave no user"

    def __init__(self, web: WebSettings, settings: AuthSettings) -> None:
        super().__init__(web, settings)
        chosen = settings if isinstance(settings, HeaderSettings) else HeaderSettings()
        self.header = chosen.header.lower()
        try:
            self.proxies = [ipaddress.ip_network(item, strict=False) for item in chosen.trusted_proxies]
        except ValueError as error:
            raise SettingsError(f"[auth.header] trusted_proxies: {error}") from error

    def describe(self) -> str:
        return f"The user of the header {self.header} of a trusted proxy"

    def from_request(self, headers: Mapping[str, str], client: str | None) -> User | None:
        name = headers.get(self.header, "").strip()
        if not name or client is None:
            return None
        try:
            address = ipaddress.ip_address(client)
        except ValueError:
            return None
        # Anyone can send the header: only a proxy that has checked the user may give it.
        if not any(address in network for network in self.proxies):
            return None
        return User(name[:80])


class NoAuth(AuthBackend):
    """No login at all, on any address."""

    @property
    def access(self) -> Access:
        return Access.OPEN

    def describe(self) -> str:
        return "No login (auth none)"


def _inspect_backend(name: str, cls: type[AuthBackend]) -> list[Problem]:
    problems = settings_problems(cls, AuthSettings)
    if any(not isinstance(item, LoginField) for item in cls.login_fields):
        problems.append(Problem(Severity.ERROR, "login_fields must hold snapdelta_web.auth.LoginField items"))
    return problems


AUTH: ExtensionPoint[type[AuthBackend]] = ExtensionPoint(
    group="snapdelta.auth",
    kind="auth backend",
    interface="snapdelta_web.auth.AuthBackend",
    table="auth",
    check=subclass_check(AuthBackend),
    inspect=_inspect_backend,
)


def auth_backend(settings: Settings) -> AuthBackend:
    """The backend of `[web] auth`, with its `[auth.NAME]` settings; a SnapdeltaError when it cannot start."""
    name = settings.web.auth
    try:
        cls = require(AUTH, name)
    except SnapdeltaError as error:
        raise SettingsError(f"[web] auth: {error}", {"web.auth": str(error)}) from error
    return cls(settings.web, settings.plugin("auth", name, cls.settings_class))


def session_value(key: bytes, user: User, now: float | None = None) -> str:
    """A cookie value that proves a login: its end time and its user, signed with the key of the backend."""
    expires = int((time.time() if now is None else now) + SESSION_TTL_S)
    name = base64.urlsafe_b64encode((user.name or "").encode()).decode()
    return f"{expires}.{name}.{_signature(key, expires, name)}"


def session_user(key: bytes, value: str | None, now: float | None = None) -> User | None:
    """The user of a session cookie, or None when it is not valid or ended."""
    expires, _, rest = (value or "").partition(".")
    name, _, signature = rest.partition(".")
    if not expires.isdigit() or int(expires) < (time.time() if now is None else now):
        return None
    if not hmac.compare_digest(signature, _signature(key, int(expires), name)):
        return None
    try:
        return User(base64.urlsafe_b64decode(name.encode()).decode() or None)
    except (binascii.Error, UnicodeDecodeError):
        return None


def valid_bearer(token: str, header: str | None) -> bool:
    scheme, _, value = (header or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), token.encode())


def valid_token(token: str, given: str) -> bool:
    return hmac.compare_digest(given.strip().encode(), token.encode())


def safe_next(path: str | None) -> str:
    """Where a login goes on: a path of this server only, never another site."""
    return path if path and path.startswith("/") and not path.startswith("//") else "/diffs"


def _signature(key: bytes, expires: int, name: str) -> str:
    derived = hashlib.sha256(b"snapdelta session " + key).digest()
    return hmac.new(derived, f"{expires}.{name}".encode(), hashlib.sha256).hexdigest()
