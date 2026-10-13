"""Access with a token: the web pages ask for it once and keep a signed session cookie; the API takes a Bearer token."""

import hashlib
import hmac
import time

from snapdelta.settings import Auth, Settings

SESSION_COOKIE = "snapdelta_session"
SESSION_TTL_S = 12 * 3600
# The login page and the files that it needs; everything else needs the token when one is set.
OPEN_PATHS = ("/login", "/static/", "/favicon.ico")


def token_of(settings: Settings) -> str | None:
    """The token of `[web] token`, or None when the server needs no token."""
    if settings.web.auth == Auth.NONE or settings.web.token is None:
        return None
    return settings.web.token.value()


def access_text(settings: Settings) -> str:
    if settings.web.auth == Auth.NONE:
        return "No login (auth none)"
    return "Token login" if settings.web.token is not None else "No login, this machine only"


def session_value(token: str, now: float | None = None) -> str:
    """A cookie value that proves a login: its end time, signed with a key that only the token gives."""
    expires = int((time.time() if now is None else now) + SESSION_TTL_S)
    return f"{expires}.{_signature(token, expires)}"


def valid_session(token: str, value: str | None, now: float | None = None) -> bool:
    expires, _, signature = (value or "").partition(".")
    if not expires.isdigit() or int(expires) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(signature, _signature(token, int(expires)))


def valid_bearer(token: str, header: str | None) -> bool:
    scheme, _, value = (header or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), token.encode())


def valid_token(token: str, given: str) -> bool:
    return hmac.compare_digest(given.strip().encode(), token.encode())


def safe_next(path: str | None) -> str:
    """Where a login goes on: a path of this server only, never another site."""
    return path if path and path.startswith("/") and not path.startswith("//") else "/diffs"


def _signature(token: str, expires: int) -> str:
    # A new token gives a new key, so changing the token ends every session.
    key = hashlib.sha256(b"snapdelta session " + token.encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()
