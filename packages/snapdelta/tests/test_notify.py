import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from snapdelta.activity import Run, RunStatus
from snapdelta.notify import NOTIFIERS, Notifier, NotifierSettings, RunEvent, describe_run, notify, run_url
from snapdelta.plugins import register_plugin
from snapdelta.settings import SettingsError, load_settings
from snapdelta.store import FileStore
from snapdelta.take import diff_snapshots, take_snapshots
from snapdelta_example import ExampleLiveSource

DEVICES = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin" / "devices"
INVENTORY = DEVICES.parent / "inventory"


class Collect(Notifier):
    events: list[RunEvent] = []

    @classmethod
    def send(cls, event: RunEvent, settings: NotifierSettings) -> None:
        cls.events.append(event)


@pytest.fixture
def collected():
    Collect.events = []
    register_plugin(NOTIFIERS, "collect", Collect)
    return Collect.events


def settings_with(tmp_path: Path, notifiers: str) -> object:
    path = tmp_path / "snapdelta.toml"
    path.write_text(
        f'[web]\npublic_url = "https://snapdelta.example.net/"\n[inventory]\nlocation = "{INVENTORY}"\n'
        f'[sources.example-live]\ndata_dir = "{DEVICES}"\n{notifiers}'
    )
    return load_settings(path)


def run(status: RunStatus = RunStatus.OK, kind: str = "diff", differences: bool | None = None) -> Run:
    return Run.model_validate(
        {
            "id": "20261014T080000000000Z-diff-abcd",
            "kind": kind,
            "origin": "cli",
            "started_at": "2026-10-14T08:00:00Z",
            "ended_at": "2026-10-14T08:00:01Z",
            "status": status,
            "targets": ["R1"],
            "outputs": ["20261014T080000Z-R1-vs-R1"] if kind == "diff" else [],
            "has_differences": differences,
        }
    )


def test_a_run_goes_to_each_notifier_whose_filter_matches(tmp_path, collected) -> None:
    settings = settings_with(
        tmp_path,
        '[notifiers.all]\ntype = "collect"\non = ["failure", "differences", "success"]\n'
        '[notifiers.failures]\ntype = "collect"\non = ["failure"]\n'
        '[notifiers.edge]\ntype = "collect"\non = ["success"]\ngroups = ["edge"]\n',
    )
    assert notify(run(), settings) == {"all": "sent"}  # type: ignore[arg-type]
    assert notify(run(RunStatus.PARTIAL, "snapshot"), settings) == {"all": "sent", "failures": "sent"}  # type: ignore[arg-type]
    assert collected[0].url == "https://snapdelta.example.net/diffs/20261014T080000Z-R1-vs-R1"
    assert collected[0].text == "snapdelta diff of R1 from cli: no difference"


def test_a_snapshot_and_a_diff_record_what_each_notifier_did(tmp_path, collected) -> None:
    settings = settings_with(tmp_path, '[notifiers.chat]\ntype = "collect"\non = ["differences", "failure"]\n')
    store = FileStore(tmp_path / "store")
    take_snapshots(store, {"R1": ExampleLiveSource, "R9": ExampleLiveSource}, "before", settings=settings)  # type: ignore[arg-type]
    [snapshot_run] = store.runs()
    assert snapshot_run.notified == {"chat": "sent"} and snapshot_run.status == RunStatus.PARTIAL
    take_snapshots(store, {"R2": ExampleLiveSource}, "after", settings=settings)  # type: ignore[arg-type]
    assert store.runs()[0].notified == {}
    done = diff_snapshots(store, settings, "R1@before", "R2@after")  # type: ignore[arg-type]
    diff_run = store.runs(kind="diff")[0]
    assert done.output.has_differences() and diff_run.has_differences is True
    assert diff_run.notified == {"chat": "sent"}
    assert collected[-1].payload()["run"]["outputs"] == [done.name]
    assert "notified" not in collected[-1].payload()["run"] and "log" not in collected[-1].payload()["run"]


def test_a_failing_notifier_never_fails_the_run(tmp_path) -> None:
    command = json.dumps([sys.executable, "-c", "import sys; sys.exit('no mail server')"])
    settings = settings_with(tmp_path, f'[notifiers.mail]\ntype = "command"\non = ["success"]\ncommand = {command}\n')
    store = FileStore(tmp_path / "store")
    taken, failures = take_snapshots(store, {"R1": ExampleLiveSource}, settings=settings)  # type: ignore[arg-type]
    assert taken and not failures
    assert store.runs()[0].notified == {"mail": "SnapdeltaError: exit code 1: no mail server"}


def test_the_command_notifier_gets_the_run_on_its_input(tmp_path) -> None:
    out = tmp_path / "event.json"
    script = f"import sys; open({str(out)!r}, 'w').write(sys.stdin.read())"
    command = json.dumps([sys.executable, "-c", script])
    settings = settings_with(tmp_path, f'[notifiers.script]\ntype = "command"\non = ["success"]\ncommand = {command}\n')
    assert notify(run(kind="snapshot"), settings) == {"script": "sent"}  # type: ignore[arg-type]
    event = json.loads(out.read_text())
    assert event["run"]["kind"] == "snapshot" and event["url"].endswith("/activity/20261014T080000000000Z-diff-abcd")


def test_the_webhook_notifier_posts_json(tmp_path, monkeypatch) -> None:
    received: list[tuple[dict[str, str], dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((dict(self.headers), json.loads(body)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HOOK_URL", f"http://127.0.0.1:{server.server_port}/hook")
    monkeypatch.setenv("HOOK_TOKEN", "Bearer t0ken")
    settings = settings_with(
        tmp_path,
        '[notifiers.hook]\ntype = "webhook"\nurl = { env = "HOOK_URL" }\n'
        'headers = { Authorization = { env = "HOOK_TOKEN" } }\non = ["differences"]\n',
    )
    try:
        assert notify(run(differences=True), settings) == {"hook": "sent"}  # type: ignore[arg-type]
    finally:
        server.shutdown()
    [(headers, body)] = received
    assert headers["Authorization"] == "Bearer t0ken"
    assert body["text"] == "snapdelta diff of R1 from cli: differences found"


def test_a_wrong_notifier_table_stops_the_settings(tmp_path) -> None:
    with pytest.raises(SettingsError) as caught:
        settings_with(tmp_path, '[notifiers.chat]\ntype = "slack"\n')
    assert caught.value.errors == {
        "notifiers.chat.type": "no notifier 'slack' is installed (installed: command, webhook)"
    }
    with pytest.raises(SettingsError) as caught:
        settings_with(tmp_path, '[notifiers.hook]\ntype = "webhook"\non = ["sometimes"]\n')
    assert set(caught.value.errors) == {"notifiers.hook.url", "notifiers.hook.on.0"}


def test_the_text_and_the_link_of_a_run() -> None:
    from snapdelta.settings import Settings

    assert describe_run(run(RunStatus.FAILED, "snapshot")) == "snapdelta snapshot of R1 from cli: failed"
    assert run_url(run(), Settings()) is None
