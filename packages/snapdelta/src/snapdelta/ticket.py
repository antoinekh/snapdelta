"""The ticket of a snapshot or a diff: free text that names the work, such as CHG-1042 or #216."""

from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator, Field

TICKET_MAX = 80


def normalize_ticket(text: str | None) -> str | None:
    """The text on one line, without the white space around and inside it; None when nothing is left."""
    if text is None:
        return None
    return " ".join(text.split()) or None


def ticket_key(text: str | None) -> str | None:
    """The form in which two tickets are the same, whatever their case."""
    ticket = normalize_ticket(text)
    return ticket.casefold() if ticket is not None else None


TicketText = Annotated[str | None, Field(default=None, max_length=TICKET_MAX), BeforeValidator(normalize_ticket)]
