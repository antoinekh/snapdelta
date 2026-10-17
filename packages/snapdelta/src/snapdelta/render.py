"""Formats of a diff output (text, JSON, Markdown, CSV, and plugin formats) and the text of a snapshot."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .errors import SnapdeltaError
from .output import Output, Side
from .plugins import ExtensionPoint, Problem, Severity, plugins, require
from .source import Source

OWN = ("-", "+")


@dataclass(frozen=True)
class Renderer:
    """A format of a diff output; a plugin declares one in the entry point group `snapdelta.renderers`."""

    media_type: str
    suffix: str
    render: Callable[[Output], str]
    # The outputs of several targets in one text, such as one JSON list; None: each output, one after the other.
    render_many: Callable[[list[Output]], str] | None = None

    def many(self, outputs: list[Output]) -> str:
        if self.render_many is not None:
            return self.render_many(outputs)
        return "\n".join(self.render(output).rstrip("\n") + "\n" for output in outputs)


@dataclass(frozen=True)
class ChangeRow:
    """One change of an output, flat: an object of one side only, or one changed attribute."""

    model: str
    name: str
    parents: tuple[str, ...]
    change: str
    attribute: str | None
    left: Any
    right: Any


def change_rows(output: Output) -> Iterator[ChangeRow]:
    """The changes of an output in diff order, each child after its parent."""
    for model, elements in output.diff.items():
        yield from _change_rows(model, elements, ())


def _change_rows(model: str, elements: dict[str, Any], parents: tuple[str, ...]) -> Iterator[ChangeRow]:
    for name, element in elements.items():
        left, right = element.get("-"), element.get("+")
        if left and right:
            for key in left:
                yield ChangeRow(model, name, parents, "changed", key, left[key], right[key])
        elif left is not None and right is None:
            yield ChangeRow(model, name, parents, "only-left", None, None, None)
        elif right is not None and left is None:
            yield ChangeRow(model, name, parents, "only-right", None, None, None)
        for child_model, children in element.items():
            if child_model not in OWN:
                yield from _change_rows(child_model, children, (*parents, name))


def render_output(output: Output) -> str:
    lines = [f"title: {output.title}"] if output.title else []
    lines += [output.description, ""] if output.description else ([""] if output.title else [])
    lines += [f"left:  {_side(output.left)}", f"right: {_side(output.right)}", ""]
    for model, elements in output.diff.items():
        lines.append(model)
        _elements(elements, 1, lines)
    if not output.diff:
        lines.append("no difference")
    summary = output.summary
    lines += [
        "",
        f"only left: {summary.get('delete', 0)}, only right: {summary.get('create', 0)}, "
        f"changed: {summary.get('update', 0)}, unchanged: {summary.get('no-change', 0)}",
    ]
    if output.skipped:
        lines.append(f"skipped (not loaded on both sides): {', '.join(output.skipped)}")
    if output.not_selected:
        lines.append(f"not selected: {', '.join(output.not_selected)}")
    lines += profile_lines(output)
    lines += review_lines(output)
    return "\n".join(lines)


def review_lines(output: Output) -> list[str]:
    """The accepted changes: one line for the count, then one per accepted change."""
    if not output.reviews:
        return []
    done, total = output.review_counts()
    lines = [f"accepted: {done} of {total} changes"]
    for key in output.change_keys():
        review = output.reviews.get(key)
        if review is not None:
            by = f" by {review.by}" if review.by else ""
            comment = f": {review.comment}" if review.comment else ""
            lines.append(f"  {key}{by}, {review.at.isoformat(timespec='seconds')}{comment}")
    return lines


def profile_lines(output: Output) -> list[str]:
    """The profile of the output and its count checks, one line each."""
    if output.profile is None:
        return []
    described = f" ({output.profile.rules.description})" if output.profile.rules.description else ""
    lines = [f"profile: {output.profile.name or 'unnamed'}{described}"]
    for check in output.profile.counts:
        verdict = "within" if check.within else "over"
        hidden = f", {check.hidden} objects of one side not shown" if check.hidden else ""
        lines.append(
            f"count {check.model}: {check.left} -> {check.right} ({_change(check.left, check.right)}), "
            f"limit {check.limit}: {verdict}{hidden}"
        )
    return lines


def _change(left: int, right: int) -> str:
    if left == 0:
        return f"{right - left:+d}"
    return f"{right - left:+d}, {(right - left) * 100 / left:+.1f}%"


def render_snapshot(adapter: Source, model: str | None = None) -> str:
    target, captured_at = adapter.require_loaded()
    label = f" [{adapter.label}]" if adapter.label else ""
    lines = [f"{target}{label} {adapter.source} {captured_at.isoformat(timespec='seconds')}"]
    lines.append(f"loaded: {', '.join(adapter.loaded)}")
    if model is not None and model not in adapter.loaded:
        raise SnapdeltaError(f"model {model} is not loaded; loaded: {', '.join(adapter.loaded)}")
    for name in [model] if model else adapter.loaded:
        lines.append(name)
        for obj in adapter.get_all(name):
            attrs = ", ".join(f"{key}={_value(value)}" for key, value in obj.get_attrs().items())
            lines.append(f"  {obj.get_unique_id()}: {attrs}")
    lines.extend(f"warning: {warning}" for warning in adapter.warnings)
    return "\n".join(lines)


def _side(side: Side) -> str:
    label = f" [{side.label}]" if side.label else ""
    return f"{side.target}{label} {side.source} {side.captured_at.isoformat(timespec='seconds')}"


def _elements(elements: dict[str, Any], depth: int, lines: list[str]) -> None:
    pad = "  " * depth
    for name, element in elements.items():
        left, right = element.get("-"), element.get("+")
        if left and right:
            lines.append(f"{pad}~ {name}")
            lines.extend(f"{pad}    {key}: {_value(left[key])} -> {_value(right[key])}" for key in left)
        elif right is not None and left is None:
            lines.append(f"{pad}+ {name}")
        elif left is not None and right is None:
            lines.append(f"{pad}- {name}")
        else:
            # Both sides hold the element and only a child differs.
            lines.append(f"{pad}  {name}")
        for child_model, children in element.items():
            if child_model in OWN:
                continue
            lines.append(f"{pad}    {child_model}")
            _elements(children, depth + 3, lines)


def _value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def render_json(output: Output) -> str:
    return output.model_dump_json(by_alias=True, indent=2)


def render_markdown(output: Output) -> str:
    """A report to paste in a ticket or a merge request: the sides, the counts, then one table per model."""
    summary = output.summary
    lines = [
        f"# {output.title or f'{output.left.target} vs {output.right.target}'}",
        "",
        *([output.description, ""] if output.description else []),
        "| Side | Target | Source | Label | Captured |",
        "|---|---|---|---|---|",
        *(
            f"| {name} | {_cell(side.target)} | {_cell(side.source)} | {_cell(side.label or '')} | "
            f"{side.captured_at.isoformat(timespec='seconds')} |"
            for name, side in (("left", output.left), ("right", output.right))
        ),
        "",
        f"Only left: {summary.get('delete', 0)}, only right: {summary.get('create', 0)}, "
        f"changed: {summary.get('update', 0)}, unchanged: {summary.get('no-change', 0)}.",
    ]
    rows_by_model: dict[str, list[ChangeRow]] = {}
    for row in change_rows(output):
        rows_by_model.setdefault(row.model, []).append(row)
    for model, rows in rows_by_model.items():
        lines += ["", f"## {model}", "", "| Object | Change | Attribute | Left | Right |", "|---|---|---|---|---|"]
        lines += [_markdown_row(row) for row in rows]
    if not rows_by_model:
        lines += ["", "No difference."]
    if output.skipped:
        lines += ["", f"Not compared, because only one side loaded them: {', '.join(output.skipped)}."]
    if output.not_selected:
        lines += ["", f"Not selected: {', '.join(output.not_selected)}."]
    if output.profile is not None:
        lines += ["", *(f"- {line[0].upper()}{line[1:]}" for line in profile_lines(output))]
    reviews = review_lines(output)
    if reviews:
        lines += ["", "## Review", "", f"{reviews[0][0].upper()}{reviews[0][1:]}.", ""]
        lines += [f"- {_cell(line.strip())}" for line in reviews[1:]]
    return "\n".join(lines) + "\n"


def render_csv(output: Output) -> str:
    """One row per object of one side only and per changed attribute, for a spreadsheet."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["model", "object", "parents", "change", "attribute", "left", "right"])
    for row in change_rows(output):
        values = [_value(row.left), _value(row.right)] if row.attribute else ["", ""]
        writer.writerow([row.model, row.name, " / ".join(row.parents), row.change, row.attribute or "", *values])
    return buffer.getvalue()


def _markdown_row(row: ChangeRow) -> str:
    values = [_value(row.left), _value(row.right)] if row.attribute else ["", ""]
    cells = [" / ".join((*row.parents, row.name)), row.change, row.attribute or "", *values]
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


TEXT = Renderer("text/plain", "txt", render_output)


def render_json_many(outputs: list[Output]) -> str:
    return json.dumps([output.model_dump(mode="json", by_alias=True) for output in outputs], indent=2)


JSON = Renderer("application/json", "json", render_json, render_json_many)
MARKDOWN = Renderer("text/markdown", "md", render_markdown)
CSV = Renderer("text/csv", "csv", render_csv)


def _check_renderer(name: str, value: Any) -> str | None:
    return None if isinstance(value, Renderer) else f"not a snapdelta.render.Renderer: {value!r}"


def sample_output() -> Output:
    """A small output with one change of each kind, to try a format on."""
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    sides = [Side(target=name, source="sample", label=None, captured_at=moment, snapshot=None) for name in ("R1", "R2")]
    diff = {
        "interface": {
            "eth0": {"-": {"mtu": 1500}, "+": {"mtu": 9000}},
            "eth1": {"-": {}},
            "eth2": {"+": {}},
        }
    }
    summary = {"create": 1, "update": 1, "delete": 1, "no-change": 0}
    return Output(
        created_at=moment,
        versions={},
        left=sides[0],
        right=sides[1],
        compared=["interface"],
        skipped=[],
        summary=summary,
        diff=diff,
    )


def _inspect_renderer(name: str, value: Renderer) -> list[Problem]:
    problems = []
    if "/" not in value.media_type:
        problems.append(Problem(Severity.ERROR, f"media_type {value.media_type!r} is not a type such as text/plain"))
    if not value.suffix or value.suffix.startswith("."):
        problems.append(Problem(Severity.WARNING, f"suffix {value.suffix!r}: give the file suffix without a dot"))
    try:
        text = value.render(sample_output())
    except Exception as error:
        problems.append(Problem(Severity.ERROR, f"render() fails on a sample diff: {type(error).__name__}: {error}"))
    else:
        if not isinstance(text, str):
            problems.append(Problem(Severity.ERROR, f"render() must return text, not {type(text).__name__}"))
    return problems


RENDERERS: ExtensionPoint[Renderer] = ExtensionPoint(
    group="snapdelta.renderers",
    kind="output format",
    interface="snapdelta.render.Renderer",
    table=None,
    check=_check_renderer,
    inspect=_inspect_renderer,
)


def renderers() -> dict[str, Renderer]:
    """The formats by name, the built-in ones included; a broken one is left out."""
    return plugins(RENDERERS)


def renderer(name: str) -> Renderer:
    return require(RENDERERS, name)
