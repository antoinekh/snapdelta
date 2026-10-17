import subprocess
import sys

import pytest
from typer.testing import CliRunner

from snapdelta.cli import app
from snapdelta.errors import SnapdeltaError
from snapdelta.scaffold import KINDS, Names, new_plugin, snapdelta_range


def test_the_names_of_a_plugin() -> None:
    names = Names.of("acme-net")
    assert (names.distribution, names.package, names.cls, names.constant) == (
        "snapdelta-acme-net",
        "snapdelta_acme_net",
        "AcmeNet",
        "ACME_NET_FORMAT",
    )
    for wrong in ("Acme", "acme_net", "-acme", "acme--net", ""):
        with pytest.raises(SnapdeltaError, match="a plugin name holds"):
            Names.of(wrong)


def test_a_plugin_of_every_kind_passes_its_own_tests(tmp_path) -> None:
    root = tmp_path / "snapdelta-acme-net"
    new_plugin("acme-net", list(KINDS), root)
    pyproject = (root / "pyproject.toml").read_text()
    assert f'dependencies = ["snapdelta{snapdelta_range()}", "snapdelta-web{snapdelta_range()}"]' in pyproject
    assert '[project.entry-points."snapdelta.stores"]\nacme-net = "snapdelta_acme_net.store:AcmeNetStore"' in pyproject
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(root), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert " passed" in done.stdout and "failed" not in done.stdout


def test_the_command_writes_a_source_by_default_and_never_over_a_project(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "new", "acme", "--path", str(tmp_path / "acme")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "acme" / "src" / "snapdelta_acme" / "source.py").is_file()
    assert not (tmp_path / "acme" / "src" / "snapdelta_acme" / "store.py").exists()
    again = runner.invoke(app, ["plugins", "new", "acme", "--path", str(tmp_path / "acme")])
    assert again.exit_code == 2 and "is not empty" in again.output
    wrong = runner.invoke(app, ["plugins", "new", "acme", "--kind", "printer", "--path", str(tmp_path / "x")])
    assert wrong.exit_code == 2 and "unknown: printer" in wrong.output
