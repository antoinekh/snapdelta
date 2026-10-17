import json

from typer.testing import CliRunner

from snapdelta.cli import app
from snapdelta.doctor import diagnose, plugin_reports
from snapdelta.plugins import Severity, extension_points, register_plugin
from snapdelta.render import RENDERERS, Renderer
from snapdelta.source import SOURCES, Source, register_source
from snapdelta.store import STORES, Store
from snapdelta_example.models import Interface

runner = CliRunner()


class NoLoad(Source):
    source = "no-load"
    interface = Interface
    top_level = ["interface"]


class HalfStore(Store):
    backend = "half"


def problems(name: str) -> list[str]:
    return [problem.message for report in plugin_reports() if report.name == name for problem in report.problems]


def test_the_installed_plugins_are_healthy() -> None:
    reports = plugin_reports()
    assert {report.group for report in reports} == {point.group for point in extension_points()}
    assert all(report.severity == Severity.OK for report in reports), [r for r in reports if r.problems]


def test_a_source_needs_a_load_and_every_model_of_its_tree() -> None:
    register_source(NoLoad)
    assert problems("no-load") == [
        "load() has no code: it must fill the models of one target",
        "interface: its child 'address', which is not a model of the source",
    ]


def test_a_store_needs_every_method_of_the_interface_and_the_name_of_its_scheme() -> None:
    register_plugin(STORES, "halfway", HalfStore)
    found = problems("halfway")
    assert found[0].startswith("these methods of the interface have no code: _delete_output, ")
    assert found[1] == "backend is 'half': give it the name of its scheme, 'halfway'"


def test_a_format_is_tried_on_a_sample_diff() -> None:
    register_plugin(RENDERERS, "crash", Renderer("text", ".txt", lambda output: str(1 / 0)))
    register_plugin(RENDERERS, "bytes", Renderer("application/pdf", "pdf", lambda output: b"%PDF"))  # type: ignore[arg-type]
    assert problems("crash") == [
        "media_type 'text' is not a type such as text/plain",
        "suffix '.txt': give the file suffix without a dot",
        "render() fails on a sample diff: ZeroDivisionError: division by zero",
    ]
    assert problems("bytes") == ["render() must return text, not bytes"]


def test_a_broken_plugin_is_an_error(fake_entry_points) -> None:
    def broken() -> object:
        raise ImportError("no module named boto3")

    fake_entry_points(STORES.group, "s3", broken, "snapdelta-s3")
    report = next(report for report in plugin_reports() if report.name == "s3")
    assert (report.package, report.severity) == ("snapdelta-s3", Severity.ERROR)
    assert report.problems[0].message == "ImportError: no module named boto3"


def test_the_setup_checks(tmp_path) -> None:
    inventory = tmp_path / "inventory"
    inventory.mkdir()
    (inventory / "hosts.yaml").write_text("R1:\n  platform: junos\nR2:\n  platform: example-live\n")
    settings = tmp_path / "snapdelta.toml"
    settings.write_text(f'[store]\nlocation = "{tmp_path / "store"}"\n[inventory]\nlocation = "inventory"\n')
    diagnosis = diagnose(settings)
    checks = {check.subject: check for check in diagnosis.checks}
    assert diagnosis.healthy and diagnosis.count(Severity.WARNING) == 1
    assert checks["store"].detail.startswith(f"file: {tmp_path / 'store'}, 0 snapshots")
    assert [problem.message for problem in checks["inventory"].problems] == [
        "no source for the platform of R1 (junos): map it in [inventory.platforms]"
    ]


def test_a_settings_error_stops_the_setup_checks(tmp_path) -> None:
    settings = tmp_path / "snapdelta.toml"
    settings.write_text("[web]\nport = 0\n")
    diagnosis = diagnose(settings)
    assert not diagnosis.healthy
    assert [check.subject for check in diagnosis.checks] == ["models", "settings"]
    assert "web.port" in diagnosis.checks[1].problems[0].message


def test_the_doctor_command_exits_1_on_an_error(tmp_path) -> None:
    store = ["--store", str(tmp_path / "store")]
    healthy = runner.invoke(app, [*store, "doctor"])
    assert healthy.exit_code == 0, healthy.output
    assert "  ok       source example (snapdelta-example " in healthy.output
    assert healthy.output.endswith("0 errors, 0 warnings\n")
    register_source(NoLoad)
    sick = runner.invoke(app, [*store, "doctor", "--json"])
    assert sick.exit_code == 1
    found = json.loads(sick.output)
    assert found["errors"] == 2
    assert next(report for report in found["plugins"] if report["name"] == "no-load")["severity"] == "error"


def test_the_plugins_command_lists_every_group() -> None:
    result = runner.invoke(app, ["plugins"])
    assert result.exit_code == 0, result.output
    assert "store backend\tfile\tsnapdelta " in result.output
    assert "output format\tmarkdown\tsnapdelta " in result.output
    assert any(SOURCES.kind in line for line in result.output.splitlines())


def test_the_plugins_in_use_come_from_the_settings(tmp_path) -> None:
    secret = tmp_path / "token"
    secret.write_text("t0ken")
    path = tmp_path / "snapdelta.toml"
    path.write_text(
        f'[store]\nlocation = "sqlite://{tmp_path / "s.db"}"\n'
        '[snapshot]\nrunner = "processes"\n'
        f'[web]\ntoken = {{ file = "{secret}" }}\n'
        '[notifiers.chat]\ntype = "webhook"\nurl = { env = "CHAT_URL" }\n'
        '[profiles.window]\nrules = { "interface.description" = "case-insensitive" }\n'
    )
    from snapdelta.settings import load_settings

    used = {(report.kind, report.name): report.used_by for report in plugin_reports(settings=load_settings(path))}
    assert used[("store backend", "sqlite")] == ["the store"] and used[("store backend", "file")] == []
    assert used[("runner", "processes")] == ["[snapshot] runner"] and used[("runner", "threads")] == []
    assert used[("secret backend", "file")] == ["web.token"]
    assert used[("secret backend", "env")] == ["notifiers.chat.url"]
    assert used[("notifier", "webhook")] == ["[notifiers.chat]"]
    assert used[("compare rule", "case-insensitive")] == ["[profiles.window]"]
    assert used[("inventory format", "nornir")] == []
    listed = runner.invoke(app, ["--settings", str(path), "plugins"])
    assert "store backend\tsqlite\tsnapdelta " in listed.output and "\tok\tthe store\n" in listed.output
