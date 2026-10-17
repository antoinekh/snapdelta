"""Notifiers: after each run, every notifier of the settings whose filter matches gets the run.

One `[notifiers.NAME]` table per notifier: `type` names its plugin of the entry point group `snapdelta.notifiers`
(`webhook` and `command` are built in), the filter chooses the runs, and the other keys are the settings of the
plugin. A notifier that fails never fails the run: the run records the error.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, ValidationError

from .activity import Run, RunStatus
from .errors import SnapdeltaError
from .plugins import ExtensionPoint, Problem, abstract_problems, discover, settings_problems, subclass_check
from .settings import Secret, Section, Settings, SettingsError, settings_error

_log = logging.getLogger(__name__)
SENT = "sent"
TIMEOUT_S = 10.0


class Trigger(StrEnum):
    """What makes a run worth a message."""

    FAILURE = "failure"
    DIFFERENCES = "differences"
    SUCCESS = "success"


class NotifierSettings(Section):
    """The keys of every `[notifiers.NAME]` table; a plugin subclass adds its own."""

    type: str = Field(description="The notifier plugin: webhook, command, or a notifier of a plugin.")
    on: list[Trigger] = Field(
        default=[Trigger.FAILURE, Trigger.DIFFERENCES],
        description=(
            "The runs to send: failure (a target or the run failed), differences (a diff that found one), "
            "success (a run with no error)."
        ),
    )
    kinds: list[str] = Field(default=["snapshot", "diff"], description="The kinds of run to send: snapshot, diff.")
    groups: list[str] = Field(
        default=[], description="Only the runs on a host of one of these inventory groups; empty: every run."
    )


@dataclass(frozen=True)
class RunEvent:
    """What a notifier gets: the run, a line that says what happened, and the link to its page."""

    run: Run
    text: str
    url: str | None

    def payload(self) -> dict[str, Any]:
        run = self.run.model_dump(mode="json", exclude={"log", "notified"})
        return {"text": self.text, "url": self.url, "run": run}


class Notifier(ABC):
    """A way to send a run: a chat, an email, a ticket; it gets the settings of one `[notifiers.NAME]` table."""

    settings_class: ClassVar[type[NotifierSettings]] = NotifierSettings

    @classmethod
    @abstractmethod
    def send(cls, event: RunEvent, settings: NotifierSettings) -> None:
        """Send the event; raise an exception when it cannot be sent."""


class WebhookSettings(NotifierSettings):
    url: Secret = Field(description="The URL that gets the run as JSON, with a text field for Slack or Teams.")
    headers: dict[str, Secret] = Field(default={}, description="Headers of the request, such as Authorization.")
    timeout_s: float = Field(default=TIMEOUT_S, gt=0)


class Webhook(Notifier):
    """A POST of the run as JSON; `text` is the line that a chat shows."""

    settings_class = WebhookSettings

    @classmethod
    def send(cls, event: RunEvent, settings: NotifierSettings) -> None:
        assert isinstance(settings, WebhookSettings)
        headers = {"Content-Type": "application/json"} | {key: value.value() for key, value in settings.headers.items()}
        body = json.dumps(event.payload()).encode()
        request = urllib.request.Request(settings.url.value(), data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=settings.timeout_s) as response:
            response.read()


class CommandSettings(NotifierSettings):
    command: list[str] = Field(
        min_length=1, description="The program and its arguments; it gets the run as JSON on its input."
    )
    timeout_s: float = Field(default=TIMEOUT_S, gt=0)


class Command(Notifier):
    """A program that gets the run as JSON on its input: a mail, a comment on a ticket, a script of your own."""

    settings_class = CommandSettings

    @classmethod
    def send(cls, event: RunEvent, settings: NotifierSettings) -> None:
        assert isinstance(settings, CommandSettings)
        done = subprocess.run(
            settings.command,
            input=json.dumps(event.payload()),
            text=True,
            capture_output=True,
            timeout=settings.timeout_s,
            check=False,
        )
        if done.returncode != 0:
            raise SnapdeltaError(f"exit code {done.returncode}: {done.stderr.strip()[:200]}")


def _inspect_notifier(name: str, cls: type[Notifier]) -> list[Problem]:
    return abstract_problems(cls) + settings_problems(cls, NotifierSettings)


NOTIFIERS: ExtensionPoint[type[Notifier]] = ExtensionPoint(
    group="snapdelta.notifiers",
    kind="notifier",
    interface="snapdelta.notify.Notifier",
    # The tables are named by the user, one per notifier, not one per plugin.
    table=None,
    check=subclass_check(Notifier),
    inspect=_inspect_notifier,
)


def notifier_settings(settings: Settings) -> dict[str, tuple[type[Notifier], NotifierSettings]]:
    """The plugin and the typed settings of each `[notifiers.NAME]` table; raises SettingsError on a bad table."""
    found: dict[str, tuple[type[Notifier], NotifierSettings]] = {}
    installed = discover(NOTIFIERS).plugins
    errors: dict[str, str] = {}
    for name, table in settings.notifiers.items():
        kind = table.get("type")
        if kind not in installed:
            names = ", ".join(sorted(installed)) or "none"
            errors[f"notifiers.{name}.type"] = f"no notifier {kind!r} is installed (installed: {names})"
            continue
        cls = installed[kind].value
        try:
            found[name] = (cls, cls.settings_class.model_validate(table))
        except ValidationError as error:
            errors |= settings_error(settings.file, ["notifiers", name], error).errors
    if errors:
        where = settings.file or "settings"
        raise SettingsError("\n".join(f"{where}: {key}: {message}" for key, message in errors.items()), errors)
    return found


def matches(run: Run, chosen: NotifierSettings, settings: Settings) -> bool:
    if run.kind not in chosen.kinds:
        return False
    failed = run.status != RunStatus.OK
    wanted = (
        (Trigger.FAILURE in chosen.on and failed)
        or (Trigger.DIFFERENCES in chosen.on and bool(run.has_differences))
        or (Trigger.SUCCESS in chosen.on and not failed)
    )
    if not wanted or not chosen.groups:
        return wanted
    inventory = settings.hosts()
    members = {host for group in chosen.groups for host in inventory.members(group)} if inventory else set()
    return any(target in members for target in run.targets)


def describe_run(run: Run) -> str:
    """One line for a chat: the kind, the status, the targets, and the result."""
    targets = ", ".join(run.targets) or "no target"
    if run.kind == "diff" and run.status == RunStatus.OK:
        result = "differences found" if run.has_differences else "no difference"
    else:
        result = run.status.value
    errors = f", {len(run.errors)} errors" if run.errors else ""
    return f"snapdelta {run.kind} of {targets} from {run.origin}: {result}{errors}"


def run_url(run: Run, settings: Settings) -> str | None:
    """The page of the diff of a run, else the page of the run, when `[web] public_url` is set."""
    base = (settings.web.public_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}/diffs/{run.output}" if run.output else f"{base}/activity/{run.id}"


def notify(run: Run, settings: Settings) -> dict[str, str]:
    """Send the run to each notifier whose filter matches it: `sent` or the error, by notifier name."""
    if not settings.notifiers:
        return {}
    event = RunEvent(run, describe_run(run), run_url(run, settings))
    results: dict[str, str] = {}
    for name, (cls, chosen) in notifier_settings(settings).items():
        try:
            if not matches(run, chosen, settings):
                continue
            cls.send(event, chosen)
        except Exception as error:
            # A notifier that fails must not fail the run, which already happened.
            results[name] = f"{type(error).__name__}: {error}"
            _log.warning("notifier %s failed: %s", name, results[name])
        else:
            results[name] = SENT
    return results


def deliver(run: Run, settings: Settings) -> Mapping[str, str]:
    """notify(), with a settings error recorded as the error of every notifier instead of raised."""
    try:
        return notify(run, settings)
    except SettingsError as error:
        return dict.fromkeys(settings.notifiers, str(error))
