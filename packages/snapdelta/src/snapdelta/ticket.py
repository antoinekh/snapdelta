"""The ticket of a snapshot or a diff: free text that names the work, such as CHG-1042 or #216."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from .errors import SnapdeltaError

TICKET_MAX = 80


def one_line(text: str | None) -> str | None:
    """The text with each white space run collapsed to one space, so it holds no line break."""
    return " ".join(text.split()) if text is not None else None


def normalize_ticket(text: str | None) -> str | None:
    """The ticket as it is saved: one line without extra white space; None when nothing is left."""
    return one_line(text) or None


def ticket_key(text: str | None) -> str | None:
    """The form in which two tickets are the same, whatever their case."""
    ticket = normalize_ticket(text)
    return ticket.casefold() if ticket is not None else None


def check_ticket(text: str | None) -> str | None:
    """The normalized ticket; a ticket that is too long fails here, before any work, not deep in a document."""
    ticket = normalize_ticket(text)
    if ticket is not None and len(ticket) > TICKET_MAX:
        raise SnapdeltaError(f"not a valid ticket: {TICKET_MAX} characters at most")
    return ticket


def _clean_text(clean: Callable[[str | None], str | None]) -> BeforeValidator:
    """Cleans a text only, so that any other value reaches the type check and fails there."""

    def validate(value: Any) -> Any:
        return clean(value) if isinstance(value, str) else value

    return BeforeValidator(validate)


TicketText = Annotated[str | None, Field(default=None, max_length=TICKET_MAX), _clean_text(normalize_ticket)]
# A ticket to set: None keeps the current one and an empty text removes it.
TicketChange = Annotated[str | None, Field(default=None, max_length=TICKET_MAX), _clean_text(one_line)]
