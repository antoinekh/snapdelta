from snapdelta.ticket import normalize_ticket, ticket_key


def test_a_ticket_is_trimmed_and_its_spaces_collapse() -> None:
    assert normalize_ticket("  CHG-1042 \t phase 2 ") == "CHG-1042 phase 2"
    assert normalize_ticket("   ") is None
    assert normalize_ticket(None) is None


def test_two_tickets_are_the_same_with_any_case() -> None:
    assert ticket_key("Straße CHG/1") == ticket_key("STRASSE chg/1")
    assert ticket_key(" ") is None


def test_a_ticket_keeps_any_character() -> None:
    assert normalize_ticket("#216 / Évolution réseau") == "#216 / Évolution réseau"
