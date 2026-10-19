import pytest

from snapdelta.errors import SnapdeltaError
from snapdelta.ticket import TICKET_MAX, check_ticket, normalize_ticket, ticket_key


def test_a_ticket_is_trimmed_and_its_spaces_collapse() -> None:
    assert normalize_ticket("  CHG-1042 \t phase 2 ") == "CHG-1042 phase 2"
    assert normalize_ticket("   ") is None
    assert normalize_ticket(None) is None


def test_two_tickets_are_the_same_with_any_case() -> None:
    assert ticket_key("Straße CHG/1") == ticket_key("STRASSE chg/1")
    assert ticket_key(" ") is None


def test_a_ticket_keeps_any_character() -> None:
    assert normalize_ticket("#216 / Évolution réseau") == "#216 / Évolution réseau"


def test_a_ticket_is_checked_before_any_work() -> None:
    assert check_ticket("  CHG-1 ") == "CHG-1" and check_ticket(None) is None and check_ticket(" ") is None
    assert check_ticket("x" * TICKET_MAX) == "x" * TICKET_MAX
    with pytest.raises(SnapdeltaError, match="not a valid ticket: 80 characters at most"):
        check_ticket("x" * (TICKET_MAX + 1))
