from typing import Any

import pytest
from typer.testing import CliRunner

from snapdelta_web import cli


@pytest.fixture
def served(monkeypatch) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **options: calls.update(options))
    return calls


def test_serves_on_localhost(served, tmp_path) -> None:
    result = CliRunner().invoke(cli.app, ["--store", str(tmp_path), "--port", "9000"])
    assert result.exit_code == 0, result.output
    assert (served["host"], served["port"], served["ssl_certfile"]) == ("127.0.0.1", 9000, None)
    assert "http://127.0.0.1:9000/diffs" in result.output and "/api/v1/docs" in result.output


def test_the_network_needs_a_token(served, tmp_path, monkeypatch) -> None:
    refused = CliRunner().invoke(cli.app, ["--store", str(tmp_path), "--host", "0.0.0.0"])
    assert refused.exit_code == 2 and "set [web] token in the settings to listen on 0.0.0.0" in refused.output
    assert served == {}
    settings = tmp_path / "snapdelta.toml"
    settings.write_text('[web]\ntoken = { env = "SNAPDELTA_TOKEN" }\n')
    monkeypatch.setenv("SNAPDELTA_TOKEN", "s3cret-token")
    args = ["--settings", str(settings), "--store", str(tmp_path / "store"), "--host", "0.0.0.0"]
    allowed = CliRunner().invoke(cli.app, args)
    assert allowed.exit_code == 0, allowed.output
    assert served["host"] == "0.0.0.0" and "no TLS: the token travels in clear" in allowed.output


def test_tls_needs_the_certificate_and_its_key(served, tmp_path) -> None:
    (tmp_path / "cert.pem").write_text("cert")
    (tmp_path / "key.pem").write_text("key")
    base = ["--store", str(tmp_path / "store")]
    half = CliRunner().invoke(cli.app, [*base, "--tls-cert", str(tmp_path / "cert.pem")])
    assert half.exit_code == 2 and "give both the TLS certificate and its key" in half.output
    both = ["--tls-cert", str(tmp_path / "cert.pem"), "--tls-key", str(tmp_path / "key.pem")]
    result = CliRunner().invoke(cli.app, [*base, *both])
    assert result.exit_code == 0 and served["ssl_keyfile"] == str(tmp_path / "key.pem")
    assert "https://127.0.0.1:8765/diffs" in result.output


def test_a_tilde_in_the_tls_options_is_expanded(served, tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = CliRunner().invoke(
        cli.app, ["--store", str(tmp_path / "store"), "--tls-cert", "~/cert.pem", "--tls-key", "~/key.pem"]
    )
    assert result.exit_code == 0, result.output
    assert (served["ssl_certfile"], served["ssl_keyfile"]) == (str(home / "cert.pem"), str(home / "key.pem"))


def test_auth_none_opens_the_network_to_the_listed_host_names(served, tmp_path) -> None:
    settings = tmp_path / "snapdelta.toml"
    settings.write_text('[web]\nauth = "none"\ntoken = "t0ken"\n')
    args = ["--settings", str(settings), "--store", str(tmp_path / "store"), "--host", "0.0.0.0"]
    refused = CliRunner().invoke(cli.app, args)
    assert refused.exit_code == 2 and "list the host names of this server in [web] allowed_hosts" in refused.output
    settings.write_text('[web]\nauth = "none"\ntoken = "t0ken"\nallowed_hosts = ["10.0.0.5"]\n')
    allowed = CliRunner().invoke(cli.app, args)
    assert allowed.exit_code == 0, allowed.output
    assert "anyone who reaches this server" in allowed.output and "[web] token is ignored" in allowed.output
    assert "no TLS" not in allowed.output
