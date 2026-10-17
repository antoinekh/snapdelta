import pytest

from snapdelta.errors import PluginError
from snapdelta.plugins import register_plugin
from snapdelta.secrets import SECRETS, SecretBackend, SecretError
from snapdelta.settings import Secret, SecretBackendSettings, SettingsError, load_settings, save_settings


class VaultSettings(SecretBackendSettings):
    prefix: str = "secret"


class FakeVault(SecretBackend):
    settings_class = VaultSettings
    values = {"secret/net#password": "from-vault", "kv/net#password": "from-kv"}

    @classmethod
    def read(cls, reference: str, settings: SecretBackendSettings) -> str:
        assert isinstance(settings, VaultSettings)
        key = f"{settings.prefix}/{reference}"
        if key not in cls.values:
            raise SecretError(f"vault: no secret {key}")
        return cls.values[key]


class Outage(SecretBackend):
    @classmethod
    def read(cls, reference: str, settings: SecretBackendSettings) -> str:
        raise ConnectionError("vault.example.net: connection refused")


def test_the_built_in_backends_are_env_and_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NET_PASSWORD", "from-env")
    assert Secret.model_validate({"env": "NET_PASSWORD"}).value() == "from-env"
    path = tmp_path / "password"
    path.write_text("from-file\n")
    secret = Secret.model_validate({"file": str(path)})
    assert (secret.value(), secret.describe(), secret.to_toml()) == ("from-file", f"file {path}", {"file": str(path)})
    with pytest.raises(SecretError, match="give an absolute path"):
        Secret(backend="file", reference="password").value()
    with pytest.raises(SecretError, match="No such file"):
        Secret(backend="file", reference=str(tmp_path / "nope")).value()


def test_a_secret_is_a_value_or_one_backend_with_its_reference() -> None:
    for wrong in ({}, {"env": ""}, {"env": "A", "file": "/b"}, {"plain": "x", "backend": "env", "reference": "A"}):
        with pytest.raises(ValueError, match="give a value, or a table with one key"):
            Secret.model_validate(wrong)
    with pytest.raises(ValueError, match=r"no secret backend 'vault' is installed \(installed: env, file\)"):
        Secret.model_validate({"vault": "net#password"})


def test_a_plugin_backend_reads_its_settings_table(tmp_path) -> None:
    register_plugin(SECRETS, "vault", FakeVault)
    path = tmp_path / "snapdelta.toml"
    path.write_text('[web]\ntoken = { vault = "net#password" }\n')
    assert load_settings(path).web.token.value() == "from-vault"  # type: ignore[union-attr]
    path.write_text('[web]\ntoken = { vault = "net#password" }\n[secrets.vault]\nprefix = "kv"\n')
    settings = load_settings(path)
    assert settings.web.token is not None and settings.web.token.value() == "from-kv"
    assert settings.web.token.describe() == "vault net#password"
    path.write_text('[web]\ntoken = { vault = "other" }\n')
    with pytest.raises(SecretError, match="vault: no secret secret/other"):
        load_settings(path).web.token.value()  # type: ignore[union-attr]


def test_an_unknown_backend_in_the_file_names_the_key(tmp_path) -> None:
    path = tmp_path / "snapdelta.toml"
    path.write_text('[web]\ntoken = { vault = "net#password" }\n')
    with pytest.raises(SettingsError) as caught:
        load_settings(path)
    assert "no secret backend 'vault' is installed" in caught.value.errors["web.token"]


def test_a_failing_backend_names_the_secret() -> None:
    register_plugin(SECRETS, "outage", Outage)
    with pytest.raises(SettingsError, match="secret outage net: ConnectionError: vault.example.net"):
        Secret(backend="outage", reference="net").value()


def test_a_backend_cannot_take_a_key_of_the_table() -> None:
    with pytest.raises(PluginError, match="'plain' is a key of the table of a secret"):
        register_plugin(SECRETS, "plain", FakeVault)


def test_a_saved_secret_keeps_its_backend(tmp_path) -> None:
    path = tmp_path / "snapdelta.toml"
    save_settings(path, {"web": {"token": Secret(backend="file", reference="/run/secrets/token")}}, installed={})
    assert 'token = {file = "/run/secrets/token"}' in path.read_text()
