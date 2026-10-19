from datetime import UTC, datetime

from snapdelta.store import SnapshotQuery
from snapdelta_web import views
from snapdelta_web.access import SnapshotCache


def cards_of(store, name, model, with_snapshots=True):
    output = store.load_output(name)
    left = SnapshotCache().side(store, output.left) if with_snapshots else None
    right = SnapshotCache().side(store, output.right) if with_snapshots else None
    return {card.name: card for card in views.build_cards(output, model, left, right)}


def test_format_value() -> None:
    assert views.format_value(None) == "none"
    assert views.format_value(True) == "true"
    assert views.format_value(["a", "b"]) == "a, b"
    assert views.format_value([]) == "empty"
    assert views.format_value(9212) == "9212"
    assert views.when(datetime(2026, 9, 25, 10, 0, tzinfo=UTC)) == "25 Sep 2026 10:00 UTC"


def test_changed_card_has_highlighted_and_grey_rows(store, names) -> None:
    card = cards_of(store, names.same, "interface")["eth0"]
    assert card.status == views.CardStatus.CHANGED
    rows = {row.name: row for row in card.rows}
    assert rows["description"] == views.Row("description", "uplink", "uplink to R2", True)
    assert rows["mtu"] == views.Row("mtu", "1500", "9000", True)
    assert rows["enabled"] == views.Row("enabled", "true", "true", False)
    assert [row.name for row in card.rows] == ["description", "enabled", "mtu", "ifindex"]
    assert card.complete


def test_a_field_that_is_never_compared_shows_as_context(store, names) -> None:
    cards = cards_of(store, names.same, "interface")
    # ifindex goes from 2 to 5 on eth1, and eth1 still has no change of its own.
    assert cards["eth1"].status == views.CardStatus.CHILDREN
    assert cards["eth1"].rows[-1] == views.Row("ifindex", "2", "5", False, context=True)
    assert cards["eth3"].rows[-1] == views.Row("ifindex", None, "4", False, context=True)


def test_child_cards_are_nested_under_their_parent(store, names) -> None:
    children = {child.name: child for child in cards_of(store, names.same, "interface")["eth0"].children}
    changed = children["eth0__10.0.0.1/30"]
    assert (changed.model, changed.status) == ("address", views.CardStatus.CHANGED)
    assert {row.name: (row.left, row.right) for row in changed.rows}["primary"] == ("true", "false")
    assert children["eth0__10.0.1.1/30"].status == views.CardStatus.ONLY_RIGHT


def test_a_parent_whose_only_change_is_a_child(store, names) -> None:
    card = cards_of(store, names.same, "interface")["eth1"]
    assert card.status == views.CardStatus.CHILDREN
    assert not any(row.changed for row in card.rows)
    assert card.children[0].status == views.CardStatus.CHANGED


def test_one_sided_cards(store, names) -> None:
    cards = cards_of(store, names.same, "interface")
    assert cards["eth2"].status == views.CardStatus.ONLY_LEFT
    assert {row.name: (row.left, row.right) for row in cards["eth2"].rows}["description"] == ("spare", None)
    assert cards["eth3"].status == views.CardStatus.ONLY_RIGHT
    assert {row.name: (row.left, row.right) for row in cards["eth3"].rows}["description"] == (None, "new link")


def test_without_snapshots_only_the_changed_values_show(store, names) -> None:
    card = cards_of(store, names.same, "interface", with_snapshots=False)["eth0"]
    assert [row.name for row in card.rows] == ["description", "mtu"]
    assert not card.complete


def test_search_text_holds_the_name_and_the_values(store, names) -> None:
    card = cards_of(store, names.same, "interface")["eth0"]
    assert "eth0" in card.search_text
    assert "uplink to r2" in card.search_text


def test_changed_cards_come_first_and_names_sort_naturally(store, names) -> None:
    output = store.load_output(names.same)
    cards = views.build_cards(output, "interface", None, None)
    assert [card.name for card in cards] == ["eth0", "eth1", "eth2", "eth3"]
    order = [card.status for card in cards]
    assert order == sorted(order, key=list(views.CardStatus).index)
    assert sorted(["1/1/10", "1/1/2"], key=views._natural) == ["1/1/2", "1/1/10"]


def test_model_deltas_and_lists(store, names) -> None:
    output = store.load_output(names.same)
    deltas = dict(views.model_deltas(output))
    assert list(deltas) == output.compared == ["interface", "vlan", "interface_state"]
    assert deltas["interface"].total == 4
    rows, unreadable, pager = views.output_rows(store, size=50)
    assert [row.name for row in rows] == [names.partial, names.same]
    assert rows[1].delta == views.output_delta(output)
    assert (rows[1].delta.only_left, rows[1].delta.only_right) == (output.summary["delete"], output.summary["create"])
    assert (unreadable, pager.has_next) == ([], False)


def test_lists_are_paged_and_filtered(big_store) -> None:
    rows, _, pager = views.output_rows(big_store, query="dev", page=2, size=50)
    assert (len(rows), pager.number, pager.has_previous, pager.has_next) == (10, 2, True, False)
    entries, pager = views.snapshot_list(big_store, SnapshotQuery(), page=36, size=50)
    assert (len(entries), pager.has_next) == (50, True)
    entries, pager = views.snapshot_list(big_store, SnapshotQuery(), page=37, size=50)
    assert (len(entries), pager.has_next) == (3, False)
    entries, pager = views.snapshot_list(big_store, SnapshotQuery(targets=("DEV000",)), page=1, size=50)
    assert (len(entries), pager.has_next, entries[0].captured_at.minute) == (50, True, 59)
    snapshots, unreadable, pager = views.target_snapshot_rows(big_store, "DEV000", page=2, size=50)
    assert (len(snapshots), unreadable, pager.has_next) == (10, [], False)
    assert snapshots[0].target == "DEV000"


def test_a_snapshot_page_has_a_next_page_only_when_a_row_follows(big_store) -> None:
    query = SnapshotQuery(targets=("DEV000",))
    for size, has_next in ((59, True), (60, False), (61, False)):
        entries, pager = views.snapshot_list(big_store, query, page=1, size=size)
        assert (len(entries), pager.has_next) == (min(size, 60), has_next)
    entries, pager = views.snapshot_list(big_store, query, page=7, size=10)
    assert (entries, pager.has_next) == ([], False)


def test_a_snapshot_page_reads_one_page_from_the_store(big_store, monkeypatch) -> None:
    asked: list[SnapshotQuery] = []
    original = big_store.snapshot_entries
    monkeypatch.setattr(big_store, "snapshot_entries", lambda query: asked.append(query) or original(query))
    views.snapshot_list(big_store, SnapshotQuery(), page=3, size=50)
    assert [(query.limit, query.offset) for query in asked] == [(51, 100)]


def test_target_matches_are_limited(big_store) -> None:
    matches, more = views.target_matches(big_store, "dev", limit=20)
    assert (len(matches), more) == (20, 30 - 20)
    assert views.target_matches(big_store, "r1", limit=20) == (["R1"], 0)


def test_unreadable_files_are_listed_not_raised(store) -> None:
    (store.outputs_dir / "20260925T120000Z-R1-vs-R2.json").write_text("{")
    _, unreadable, _ = views.output_rows(store, size=50)
    assert unreadable == ["20260925T120000Z-R1-vs-R2"]


def test_model_tables(store, names) -> None:
    tables = {table.model: table for table in views.model_tables(store.load_snapshot(names.before), 50)}
    assert list(tables) == ["interface", "address", "vlan", "interface_state"]
    assert tables["vlan"].columns == ["name"]
    assert ("10", ["users"]) in tables["vlan"].rows


def test_model_table_pages(store, names) -> None:
    first = views.model_table(store.load_snapshot(names.before), "interface", 0, 2)
    assert [name for name, _ in first.rows] == ["eth0", "eth1"]
    assert (first.total, first.next_offset, first.remaining) == (3, 2, 1)
    last = views.model_table(store.load_snapshot(names.before), "interface", 2, 2)
    assert ([name for name, _ in last.rows], last.next_offset) == (["eth2"], None)


def test_size_text() -> None:
    assert views.size_text(512) == "512 B"
    assert views.size_text(28672) == "28.0 KiB"
    assert views.size_text(5 * 1048576) == "5.0 MiB"


def test_a_delta_gives_the_share_of_each_part() -> None:
    delta = views.Delta(only_left=1, only_right=2, changed=1, unchanged=4)
    assert delta.total == 8
    assert delta.parts() == [
        ("only-left", 1, 12.5),
        ("only-right", 2, 25.0),
        ("changed", 1, 12.5),
        ("unchanged", 4, 50.0),
    ]
    assert views.Delta().parts() == [
        ("only-left", 0, 0.0),
        ("only-right", 0, 0.0),
        ("changed", 0, 0.0),
        ("unchanged", 0, 0.0),
    ]


def test_model_deltas_count_each_status(store, names) -> None:
    output = store.load_output(names.same)
    interface = dict(views.model_deltas(output))["interface"]
    statuses = [card.status for card in views.build_cards(output, "interface", None, None)]
    assert interface.only_left == statuses.count(views.CardStatus.ONLY_LEFT)
    assert interface.only_right == statuses.count(views.CardStatus.ONLY_RIGHT)
    assert interface.changed == statuses.count(views.CardStatus.CHANGED) + statuses.count(views.CardStatus.CHILDREN)
    assert views.output_delta(output).unchanged == output.summary["no-change"]


def test_a_child_label_drops_the_identifiers_of_its_parent(store, names) -> None:
    children = {child.name: child for child in cards_of(store, names.same, "interface")["eth0"].children}
    assert children["eth0__10.0.0.1/30"].label == "10.0.0.1/30"
    assert views.Card("address", "a__b__c", views.CardStatus.CHANGED, []).label == "a · b · c"


def test_a_parent_stays_when_a_child_matches_the_filters(store, names) -> None:
    eth0 = cards_of(store, names.same, "interface")["eth0"]
    kept = eth0.filtered([views.CardStatus.ONLY_RIGHT], "")
    assert kept is not None and [child.status for child in kept.children] == [views.CardStatus.ONLY_RIGHT]
    assert eth0.filtered([views.CardStatus.ONLY_LEFT], "") is None


def test_card_page(store, names) -> None:
    cards = list(cards_of(store, names.same, "interface").values())
    page = views.card_page(cards, views.FILTER_STATUSES, "", 0, 1)
    assert (len(page.cards), page.total, page.next_offset) == (1, len(cards), 1 if len(cards) > 1 else None)


def test_output_rows_read_each_output_once_with_a_cache(store, names, monkeypatch) -> None:
    from snapdelta_web.access import LruCache

    reads = []
    load = store.load_output
    monkeypatch.setattr(store, "load_output", lambda name: reads.append(name) or load(name))
    cache: views.RowCache = LruCache(10)
    first, _, _ = views.output_rows(store, size=50, cache=cache)
    again, _, _ = views.output_rows(store, size=50, cache=cache)
    assert first == again and sorted(reads) == sorted(row.name for row in first)


def test_ticks_show_names_as_code_and_escape_the_rest() -> None:
    assert str(views.ticks("its `routes` <b>")) == "its <code>routes</code> &lt;b&gt;"


def test_the_timeline_compares_each_snapshot_with_the_one_before(store, names) -> None:
    from snapdelta_web.access import LruCache

    cache = LruCache(16)
    built = views.timeline(store, "R1", None, None, cache)
    assert [point.reference for point in built.points] == [names.before, names.after, names.config]
    first, second = built.steps
    assert (first.left, first.right) == (names.before, names.after) and first.changes > 0
    # The config-only snapshot has fewer models: the step compares the models in common.
    assert second.delta is not None and second.changes == 0
    assert built.largest == first.changes and built.older == 0
    assert views.timeline(store, "R1", None, None, cache, size=2).older == 1
    assert views.timeline(store, "R1", None, None, cache).steps[0] is first


def test_marked_values_show_the_changed_words_and_items() -> None:
    left, right = views.marked_values("trunk to r2, vlan 10", "trunk to the other router, vlan 10")
    assert left == "trunk to <mark>r2</mark>, vlan 10"
    assert right == "trunk to <mark>the other router</mark>, vlan 10"
    items = views.marked_values(["10.0.0.1/30", "10.0.1.1/30"], ["10.0.1.1/30", "10.0.2.1/30"])
    assert items == ("<mark>10.0.0.1/30</mark>, 10.0.1.1/30", "10.0.1.1/30, <mark>10.0.2.1/30</mark>")
    assert views.marked_values("short", "other") is None
    assert views.marked_values("an entirely different text", "nothing in common at all here") is None
    assert views.marked_values("<b>bold</b> text and more", "<b>bold</b> text and less")[0] == (
        "&lt;b&gt;bold&lt;/b&gt; text and <mark>more</mark>"
    )


def test_diffs_group_by_day(store, names) -> None:
    from dataclasses import replace

    row = views.output_rows(store)[0][0]
    rows = [
        replace(row, ticket="CHG-1"),
        replace(row, ticket="CHG-1"),
        replace(row, note="other"),
        replace(row, ticket="CHG-1", created_at=datetime(2026, 9, 24, 9, 0, tzinfo=UTC)),
    ]
    groups = views.output_groups(rows)
    assert [(group.day, len(group.rows)) for group in groups] == [("25 Sep 2026", 3), ("24 Sep 2026", 1)]


def test_the_snapshots_filters_keep_each_target_encoded() -> None:
    query = views.snapshot_query([" A&B ", "", "<c>", "A&B"], " before ", " chg-1 ")
    assert query == SnapshotQuery(targets=("A&B", "<c>"), label="before", ticket="chg-1")
    assert views.snapshot_filters(query) == "target=A%26B&target=%3Cc%3E&label=before&ticket=chg-1"
    assert views.snapshot_filters(views.snapshot_query([], "", "")) == ""
