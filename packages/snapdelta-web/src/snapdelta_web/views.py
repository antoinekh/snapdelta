"""Core objects -> view objects for the templates, with no model or attribute named here."""

from __future__ import annotations

import difflib
import functools
import itertools
import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlencode

from markupsafe import Markup, escape

from snapdelta.activity import LogLine
from snapdelta.catalog import context_fields
from snapdelta.errors import SnapdeltaError
from snapdelta.output import Output, Review, Side, change_key, compare
from snapdelta.profile import accepted
from snapdelta.settings import Profile
from snapdelta.source import Source
from snapdelta.store import SnapshotEntry, Store

from .access import LruCache

OWN = ("-", "+")
TICKS_RE = re.compile(r"`([^`]+)`")
# diffsync joins the identifiers of an object with this into its unique id.
ID_SEPARATOR = "__"


class CardStatus(StrEnum):
    """In page order: the changes first, the objects of one side after."""

    CHANGED = "changed"
    CHILDREN = "children"
    ONLY_LEFT = "only-left"
    ONLY_RIGHT = "only-right"


@dataclass(frozen=True)
class Row:
    name: str
    left: str | None
    right: str | None
    changed: bool
    # Why the profile of the compare accepts a value that differs: "ignored" or "within tolerance".
    accepted: str | None = None
    # A field that the snapshot keeps but that diffsync never compares.
    context: bool = False
    # The values with their changed part marked, for a long text or a list.
    left_marked: Markup | None = None
    right_marked: Markup | None = None


@dataclass
class Card:
    model: str
    name: str
    status: CardStatus
    rows: list[Row]
    children: list[Card] = field(default_factory=list)
    complete: bool = True
    parent: str | None = None
    review: Review | None = None

    @property
    def key(self) -> str:
        return change_key(self.model, self.name)

    @property
    def reviewable(self) -> bool:
        """A card with a change of its own; a parent whose only change is a child has nothing to accept."""
        return self.status != CardStatus.CHILDREN

    @property
    def label(self) -> str:
        """The name to show: under its parent, a child drops the identifiers that repeat the parent."""
        own = self.name.removeprefix(f"{self.parent}{ID_SEPARATOR}") if self.parent else self.name
        return id_label(own)

    @property
    def search_text(self) -> str:
        values = [value for row in self.rows for value in (row.left, row.right) if value is not None]
        return " ".join([self.name, *values]).lower()

    def filtered(self, statuses: Collection[str], query: str) -> Card | None:
        """This card with only its matching children, or None; a parent stays when one of its children matches."""
        children = [kept for child in self.children if (kept := child.filtered(statuses, query))]
        own = self.status in statuses and (not query or query in self.search_text)
        return replace(self, children=children) if own or children else None


@dataclass(frozen=True)
class Delta:
    """How many objects of a compare are on one side only, changed or unchanged."""

    only_left: int = 0
    only_right: int = 0
    changed: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.only_left + self.only_right + self.changed + self.unchanged

    def parts(self) -> list[tuple[str, int, float]]:
        """Each part with its status, its count and its percent of the total, for the delta strips."""
        counts = [
            ("only-left", self.only_left),
            ("only-right", self.only_right),
            ("changed", self.changed),
            ("unchanged", self.unchanged),
        ]
        total = self.total
        return [(status, count, count * 100 / total if total else 0.0) for status, count in counts]


@dataclass(frozen=True)
class OutputRow:
    name: str
    created_at: datetime
    left: Side
    right: Side
    delta: Delta
    title: str | None = None
    # Accepted changes, and all the changes.
    reviewed: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class SnapshotRow:
    reference: str
    target: str
    source: str
    label: str | None
    captured_at: datetime
    loaded: list[str]
    warnings: int
    unmapped: int


@dataclass(frozen=True)
class ModelTable:
    """One page of the objects of one model; `offset` is where the page starts in all `total` objects."""

    model: str
    columns: list[str]
    rows: list[tuple[str, list[str]]]
    offset: int
    total: int
    # The last columns: kept in the snapshot, never compared.
    context: list[str] = field(default_factory=list)

    @property
    def next_offset(self) -> int | None:
        end = self.offset + len(self.rows)
        return end if end < self.total else None

    @property
    def remaining(self) -> int:
        return self.total - self.offset - len(self.rows)


def format_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(format_value(item) for item in value) or "empty"
    return str(value)


# A shorter text reads at a glance; a longer one gets its changed words marked.
MARK_MIN_LENGTH = 16
# Below this share of words in common, the whole text changed and marks would only add noise.
MARK_MIN_RATIO = 0.3
WORD_RE = re.compile(r"(\s+|[,;:/|()\[\]{}=])")


def marked_values(left: Any, right: Any) -> tuple[Markup, Markup] | None:
    """The two values with their changed part in `<mark>`: the items of two lists, the words of two long texts."""
    if isinstance(left, list) and isinstance(right, list):
        return _marked_items(left, right), _marked_items(right, left)
    if not isinstance(left, str) or not isinstance(right, str) or max(len(left), len(right)) < MARK_MIN_LENGTH:
        return None
    left_words, right_words = WORD_RE.split(left), WORD_RE.split(right)
    words_only = difflib.SequenceMatcher(None, left.split(), right.split(), autojunk=False)
    if words_only.ratio() < MARK_MIN_RATIO:
        return None
    matcher = difflib.SequenceMatcher(None, left_words, right_words, autojunk=False)
    left_parts: list[Markup] = []
    right_parts: list[Markup] = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left_text, right_text = "".join(left_words[left_start:left_end]), "".join(right_words[right_start:right_end])
        left_parts.append(escape(left_text) if tag == "equal" else _mark(left_text))
        right_parts.append(escape(right_text) if tag == "equal" else _mark(right_text))
    return Markup("").join(left_parts), Markup("").join(right_parts)


def _marked_items(items: list[Any], others: list[Any]) -> Markup:
    if not items:
        return Markup("empty")
    parts = [escape(format_value(item)) if item in others else _mark(format_value(item)) for item in items]
    return Markup(", ").join(parts)


def _mark(text: str) -> Markup:
    return Markup("<mark>{}</mark>").format(text) if text else Markup("")


def id_label(unique_id: str) -> str:
    """A unique id as a reader sees it: its identifiers, `eth1.10__r2` as `eth1.10 · r2`."""
    return unique_id.replace(ID_SEPARATOR, " · ")


def ticks(text: str) -> Markup:
    """A docstring with its `names` as code; the text is escaped first, so only the code tags are HTML."""
    return Markup(TICKS_RE.sub(r"<code>\1</code>", str(escape(text))))


def size_text(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1048576:
        return f"{size_bytes / 1024:.1f} KiB"
    return f"{size_bytes / 1048576:.1f} MiB"


def when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%d %b %Y %H:%M UTC")


def output_delta(output: Output) -> Delta:
    summary = output.summary
    return Delta(
        only_left=summary.get("delete", 0),
        only_right=summary.get("create", 0),
        changed=summary.get("update", 0),
        unchanged=summary.get("no-change", 0),
    )


def model_deltas(output: Output) -> list[tuple[str, Delta]]:
    """The top-level differences of each compared model; diffsync gives no per-model count of unchanged objects."""
    deltas = []
    for model in output.compared:
        statuses = [_status(element) for element in output.diff.get(model, {}).values()]
        deltas.append(
            (
                model,
                Delta(
                    only_left=statuses.count(CardStatus.ONLY_LEFT),
                    only_right=statuses.count(CardStatus.ONLY_RIGHT),
                    changed=statuses.count(CardStatus.CHANGED) + statuses.count(CardStatus.CHILDREN),
                ),
            )
        )
    return deltas


def _status(element: dict[str, Any]) -> CardStatus:
    left, right = element.get("-"), element.get("+")
    if left is not None and right is None:
        return CardStatus.ONLY_LEFT
    if right is not None and left is None:
        return CardStatus.ONLY_RIGHT
    return CardStatus.CHANGED if left else CardStatus.CHILDREN


def build_cards(output: Output, model: str, left: Source | None, right: Source | None) -> list[Card]:
    profile = output.profile.rules if output.profile else None
    elements = output.diff.get(model, {})
    cards = [_card(model, name, element, left, right, profile) for name, element in elements.items()]
    _attach_reviews(cards, output.reviews)
    order = list(CardStatus)
    return sorted(cards, key=lambda card: (order.index(card.status), _natural(card.name)))


def _attach_reviews(cards: list[Card], reviews: dict[str, Review]) -> None:
    for card in cards:
        card.review = reviews.get(card.key)
        _attach_reviews(card.children, reviews)


def _natural(name: str) -> list[str | int]:
    """`1/1/2` before `1/1/10`: the digit runs compare as numbers."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def _card(
    model: str,
    name: str,
    element: dict[str, Any],
    left: Source | None,
    right: Source | None,
    profile: Profile | None,
    parent: str | None = None,
) -> Card:
    left_changes, right_changes = element.get("-"), element.get("+")
    children = [
        _card(child_model, child_name, child_element, left, right, profile, name)
        for child_model, child_elements in element.items()
        if child_model not in OWN
        for child_name, child_element in child_elements.items()
    ]
    if left_changes is not None and right_changes is None:
        names = _attribute_names(model, left, left_changes)
        rows = [Row(key, format_value(left_changes[key]), None, True) for key in names if key in left_changes]
        rows += _context_rows(model, name, left, None)
        return Card(model, name, CardStatus.ONLY_LEFT, rows, children, parent=parent)
    if right_changes is not None and left_changes is None:
        names = _attribute_names(model, right, right_changes)
        rows = [Row(key, None, format_value(right_changes[key]), True) for key in names if key in right_changes]
        rows += _context_rows(model, name, None, right)
        return Card(model, name, CardStatus.ONLY_RIGHT, rows, children, parent=parent)
    left_changes, right_changes = left_changes or {}, right_changes or {}
    status = CardStatus.CHANGED if left_changes else CardStatus.CHILDREN
    left_attrs, right_attrs = _attrs(left, model, name), _attrs(right, model, name)
    complete = left_attrs is not None and right_attrs is not None
    rows = []
    for key in _attribute_names(model, left or right, left_changes):
        if key in left_changes:
            left_value, right_value = left_changes[key], right_changes[key]
            marks = marked_values(left_value, right_value) or (None, None)
            rows.append(Row(key, format_value(left_value), format_value(right_value), True, None, False, *marks))
        elif left_attrs is not None and right_attrs is not None:
            left_value, right_value = left_attrs.get(key), right_attrs.get(key)
            why = accepted(profile, model, key, left_value, right_value) if left_value != right_value else None
            rows.append(Row(key, format_value(left_value), format_value(right_value), False, why))
    if complete:
        rows += _context_rows(model, name, left, right)
    return Card(model, name, status, rows, children, complete, parent)


def _context_rows(model: str, name: str, left: Source | None, right: Source | None) -> list[Row]:
    left_obj = left.get_or_none(model, name) if left is not None else None
    right_obj = right.get_or_none(model, name) if right is not None else None
    present = left_obj or right_obj
    if present is None:
        return []
    return [
        Row(
            key,
            format_value(getattr(left_obj, key)) if left_obj is not None else None,
            format_value(getattr(right_obj, key)) if right_obj is not None else None,
            False,
            context=True,
        )
        for key in context_fields(type(present))
    ]


def _attribute_names(model: str, adapter: Source | None, known: dict[str, Any]) -> list[str]:
    """The attributes of the model class of the source, else the keys that the diff holds."""
    model_class = getattr(type(adapter), model, None) if adapter is not None else None
    if model_class is not None:
        return list(model_class._attributes)
    return list(known)


def _attrs(adapter: Source | None, model: str, name: str) -> dict[str, Any] | None:
    if adapter is None:
        return None
    obj = adapter.get_or_none(model, name)
    return obj.get_attrs() if obj is not None else None


FILTER_STATUSES = (CardStatus.ONLY_LEFT, CardStatus.ONLY_RIGHT, CardStatus.CHANGED)


@dataclass(frozen=True)
class CardFilters:
    statuses: tuple[str, ...]
    query: str

    @classmethod
    def parse(cls, statuses: list[str] | None, filtered: bool, query: str) -> CardFilters:
        """`filtered` tells "no status checked" from "no filter yet", which shows every status."""
        wanted = [status for status in FILTER_STATUSES if status in (statuses or [])]
        return cls(tuple(wanted) if filtered else FILTER_STATUSES, query.strip())

    @property
    def active(self) -> bool:
        return bool(self.query) or self.statuses != FILTER_STATUSES

    def query_string(self, **extra: str | int) -> str:
        """The filters as URL parameters, so that a link or "Show more" keeps them."""
        pairs: list[tuple[str, str | int]] = [("status", status) for status in self.statuses]
        pairs += [("filtered", "true")] if self.statuses != FILTER_STATUSES else []
        pairs += [("q", self.query)] if self.query else []
        pairs += list(extra.items())
        return urlencode(pairs)


@dataclass(frozen=True)
class CardPage:
    cards: list[Card]
    offset: int
    total: int
    size: int

    @property
    def next_offset(self) -> int | None:
        end = self.offset + self.size
        return end if end < self.total else None

    @property
    def remaining(self) -> int:
        return max(self.total - self.offset - self.size, 0)


def card_page(cards: list[Card], statuses: Collection[str], query: str, offset: int, size: int) -> CardPage:
    """The cards that match the filters, from `offset`; the browser gets one page, not thousands of cards."""
    wanted = query.strip().lower()
    matching = [kept for card in cards if (kept := card.filtered(statuses, wanted))]
    start = max(offset, 0)
    return CardPage(matching[start : start + size], start, len(matching), size)


@dataclass(frozen=True)
class Pager:
    number: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True)
class TargetRow:
    name: str
    count: int
    latest: datetime | None


def page_of[T](items: list[T], page: int, size: int) -> tuple[list[T], Pager]:
    """One page of a list; the lists come from names only, so only the page is read further."""
    number = max(page, 1)
    start = (number - 1) * size
    return items[start : start + size], Pager(number, number > 1, len(items) > start + size)


RowCache = LruCache[tuple[str, str], "OutputRow"]


def output_rows(
    store: Store, query: str = "", page: int = 1, size: int = 50, cache: RowCache | None = None
) -> tuple[list[OutputRow], list[str], Pager]:
    """One page of the outputs; `cache` keeps the small rows, so that a page does not read whole outputs again."""
    entries, pager = page_of(store.output_entries(query), page, size)
    rows: list[OutputRow] = []
    unreadable: list[str] = []
    for entry in entries:
        try:
            load = functools.partial(_output_row, store, entry.name)
            rows.append(load() if cache is None else cache.get((store.location, entry.name), load))
        except SnapdeltaError:
            unreadable.append(entry.name)
    return rows, unreadable, pager


PALETTE_PAGES = [
    ("Diffs", "/diffs"),
    ("Snapshots", "/snapshots"),
    ("New diff", "/new"),
    ("Take snapshots", "/take"),
    ("Admin: Store", "/admin"),
    ("Admin: Settings", "/admin/settings"),
    ("Admin: Profiles", "/admin/profiles"),
    ("Admin: Inventory", "/admin/inventory"),
    ("Admin: Models", "/admin/models"),
    ("Admin: About", "/admin/about"),
    ("API docs", "/api/v1/docs"),
    ("MCP server", "/mcp/docs"),
]
PALETTE_LIMIT = 8
# The palette searches the titles of the newest diffs only: each one is a row to read.
PALETTE_DIFFS = 300


@dataclass(frozen=True)
class PaletteItem:
    label: str
    url: str
    detail: str | None = None


@dataclass(frozen=True)
class PaletteGroup:
    title: str
    items: list[PaletteItem]


def palette(store: Store, query: str, cache: RowCache | None = None) -> list[PaletteGroup]:
    """The targets, the diffs and the pages that hold every word of `query`; the newest diffs when it is empty."""
    words = query.lower().split()

    def hit(*texts: str | None) -> bool:
        joined = " ".join(text for text in texts if text).lower()
        return all(word in joined for word in words)

    targets = [PaletteItem(target, f"/snapshots/{quote(target, safe='')}") for target in store.targets() if hit(target)]
    rows, _, _ = output_rows(store, "", 1, PALETTE_DIFFS, cache)
    diffs = [
        PaletteItem(row.title or f"{row.left.target} vs {row.right.target}", f"/diffs/{row.name}", when(row.created_at))
        for row in rows
        if hit(row.title, row.left.target, row.right.target, row.left.label, row.right.label)
    ]
    pages = [PaletteItem(label, url) for label, url in PALETTE_PAGES if hit(label)]
    groups = [
        PaletteGroup("Targets", targets[:PALETTE_LIMIT] if words else []),
        PaletteGroup("Diffs", diffs[: PALETTE_LIMIT if words else 5]),
        PaletteGroup("Pages", pages),
    ]
    return [group for group in groups if group.items]


# The change of a diff is the start of its title, before ": ", as in "CHG-1042: VLAN 20 on the trunk".
CHANGE_RE = re.compile(r"^([^:]{1,40}):\s")


@dataclass(frozen=True)
class OutputGroup:
    """Rows in a row of the list with the same day, and the same change when they have one."""

    day: str
    new_day: bool
    change: str | None
    rows: list[OutputRow]

    @property
    def reviewed(self) -> tuple[int, int]:
        return sum(row.reviewed[0] for row in self.rows), sum(row.reviewed[1] for row in self.rows)


def change_of(title: str | None) -> str | None:
    found = CHANGE_RE.match(title or "")
    return found.group(1).strip() if found else None


def output_groups(rows: list[OutputRow]) -> list[OutputGroup]:
    """The rows by day, newest first, and inside a day the rows of one change together under its name."""
    groups: list[OutputGroup] = []
    for row in rows:
        day = row.created_at.astimezone(UTC).strftime("%d %b %Y")
        change = change_of(row.title)
        last = groups[-1] if groups else None
        if last is not None and last.day == day and change is not None and last.change == change:
            last.rows.append(row)
        else:
            groups.append(OutputGroup(day, last is None or last.day != day, change, [row]))
    return groups


def _output_row(store: Store, name: str) -> OutputRow:
    output = store.load_output(name)
    delta = output_delta(output)
    return OutputRow(name, output.created_at, output.left, output.right, delta, output.title, output.review_counts())


def target_rows(store: Store, query: str = "", page: int = 1, size: int = 50) -> tuple[list[TargetRow], Pager]:
    names, pager = page_of(store.targets(query), page, size)
    rows = []
    for name in names:
        entries = store.snapshot_entries(name)
        rows.append(TargetRow(name, len(entries), entries[0].captured_at if entries else None))
    return rows, pager


PERIOD_FORMAT = "%Y-%m-%dT%H:%M"


@dataclass(frozen=True)
class Period:
    """The snapshots captured from `since` to `until`, both included, in UTC; None leaves that end open."""

    since: datetime | None = None
    until: datetime | None = None

    @classmethod
    def parse(cls, since: str, until: str) -> Period:
        return cls(_period_end(since), _period_end(until))

    @property
    def active(self) -> bool:
        return self.since is not None or self.until is not None

    @property
    def since_text(self) -> str:
        return self.since.strftime(PERIOD_FORMAT) if self.since else ""

    @property
    def until_text(self) -> str:
        return self.until.strftime(PERIOD_FORMAT) if self.until else ""

    @property
    def query(self) -> str:
        ends = (("since", self.since_text), ("until", self.until_text))
        return urlencode([(name, text) for name, text in ends if text])

    def select(self, entries: list[SnapshotEntry]) -> list[SnapshotEntry]:
        # The fields give minutes: a snapshot of 13:10:45 is in a period that ends at 13:10.
        return [
            entry
            for entry in entries
            if (self.since is None or entry.captured_at >= self.since)
            and (self.until is None or entry.captured_at.replace(second=0, microsecond=0) <= self.until)
        ]


ALL_TIME = Period()


def _period_end(text: str) -> datetime | None:
    if not text.strip():
        return None
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError as error:
        raise SnapdeltaError(f"{text!r} is not a date and time") from error
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def target_snapshot_rows(
    store: Store, target: str, page: int = 1, size: int = 50, period: Period = ALL_TIME
) -> tuple[list[SnapshotRow], list[str], Pager]:
    entries, pager = page_of(period.select(store.snapshot_entries(target)), page, size)
    rows: list[SnapshotRow] = []
    unreadable: list[str] = []
    for entry in entries:
        try:
            snapshot = store.read_snapshot(entry.reference)
        except SnapdeltaError:
            unreadable.append(entry.reference)
            continue
        rows.append(
            SnapshotRow(
                reference=entry.reference,
                target=snapshot.target,
                source=snapshot.source,
                label=snapshot.label,
                captured_at=snapshot.captured_at,
                loaded=snapshot.loaded,
                warnings=len(snapshot.warnings),
                unmapped=len(snapshot.unmapped),
            )
        )
    return rows, unreadable, pager


LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


def log_at_level(lines: list[LogLine], level: str) -> list[LogLine]:
    """The lines at `level` and above."""
    wanted = LOG_LEVELS.index(level) if level in LOG_LEVELS else 0
    return [line for line in lines if line.level in LOG_LEVELS and LOG_LEVELS.index(line.level) >= wanted]


# The newest snapshots of the timeline: each step is a compare, so a long history stays fast.
TIMELINE_SIZE = 40


@dataclass(frozen=True)
class TimelinePoint:
    reference: str
    captured_at: datetime
    label: str | None


@dataclass(frozen=True)
class TimelineStep:
    """The delta from one snapshot to the next; `error` when the two cannot compare."""

    left: str
    right: str
    delta: Delta | None
    error: str | None = None

    @property
    def changes(self) -> int:
        return self.delta.only_left + self.delta.only_right + self.delta.changed if self.delta else 0


@dataclass(frozen=True)
class Timeline:
    """`steps[i]` goes from `points[i]` to `points[i + 1]`, oldest first."""

    points: list[TimelinePoint]
    steps: list[TimelineStep]
    older: int

    @property
    def largest(self) -> int:
        return max((step.changes for step in self.steps), default=0) or 1


type StepCache = LruCache[tuple[str, str, str, str | None], TimelineStep]


def timeline(
    store: Store,
    target: str,
    rules: Profile | None,
    profile: str | None,
    cache: StepCache,
    size: int = TIMELINE_SIZE,
    period: Period = ALL_TIME,
) -> Timeline:
    entries = period.select(store.snapshot_entries(target))
    shown = list(reversed(entries[:size]))
    points = [TimelinePoint(entry.reference, entry.captured_at, entry.label) for entry in shown]
    steps = [
        cache.get(
            (store.location, before.reference, after.reference, profile),
            functools.partial(_timeline_step, store, before.reference, after.reference, rules, profile),
        )
        for before, after in itertools.pairwise(shown)
    ]
    return Timeline(points, steps, max(len(entries) - size, 0))


def _timeline_step(store: Store, left: str, right: str, rules: Profile | None, profile: str | None) -> TimelineStep:
    try:
        output = compare(store.load_snapshot(left), store.load_snapshot(right), None, rules, profile)
    except SnapdeltaError as error:
        return TimelineStep(left, right, None, str(error))
    return TimelineStep(left, right, output_delta(output))


def target_matches(
    store: Store, query: str, limit: int = 20, members: Collection[str] | None = None
) -> tuple[list[str], int]:
    """The first matching targets, and how many more match; `members` keeps the hosts of one inventory group."""
    names = [name for name in store.targets(query) if members is None or name in members]
    return names[:limit], max(len(names) - limit, 0)


def model_tables(adapter: Source, size: int) -> list[ModelTable]:
    """The first page of each loaded model: a page of thousands of rows is slow to build and to show."""
    return [model_table(adapter, model, 0, size) for model in adapter.loaded]


def model_table(adapter: Source, model: str, offset: int, size: int) -> ModelTable:
    columns = _attribute_names(model, adapter, {})
    model_class = getattr(type(adapter), model, None)
    context = context_fields(model_class) if model_class is not None else []
    objects = sorted(adapter.get_all(model), key=lambda obj: _natural(obj.get_unique_id()))
    start = max(offset, 0)
    rows = [
        (
            obj.get_unique_id(),
            [format_value(obj.get_attrs().get(key)) for key in columns]
            + [format_value(getattr(obj, key)) for key in context],
        )
        for obj in objects[start : start + size]
    ]
    return ModelTable(model, columns, rows, start, len(objects), context)


@dataclass(frozen=True)
class ToolParameter:
    name: str
    type: str
    required: bool
    default: str | None
    description: str | None


def schema_type(schema: dict[str, Any]) -> str:
    """A JSON schema type as a reader writes it: `string`, `array of string`, `string | null`."""
    if "anyOf" in schema:
        return " | ".join(schema_type(option) for option in schema["anyOf"])
    if schema.get("type") == "array":
        return f"array of {schema_type(schema.get('items', {}))}"
    return str(schema.get("type", "object"))


def tool_parameters(input_schema: dict[str, Any]) -> list[ToolParameter]:
    required = set(input_schema.get("required", []))
    return [
        ToolParameter(
            name,
            schema_type(schema),
            name in required,
            json.dumps(schema["default"]) if "default" in schema else None,
            schema.get("description"),
        )
        for name, schema in input_schema.get("properties", {}).items()
    ]


def example_arguments(input_schema: dict[str, Any]) -> str:
    """The required arguments of a tool with an empty value of their type, to start a Try form."""
    empty = {"string": "", "integer": 0, "number": 0, "boolean": False, "array": [], "object": {}}
    properties = input_schema.get("properties", {})
    values = {
        name: empty.get(str(properties.get(name, {}).get("type")), "") for name in input_schema.get("required", [])
    }
    return json.dumps(values, indent=2)
