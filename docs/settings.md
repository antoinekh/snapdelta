# Settings

snapdelta reads its settings from one TOML file, `snapdelta.toml`: the store, the snapshots, the web UI, and the settings of each source. Every key is optional; without a file, the defaults below apply.

## The file

The values below are the defaults.

```toml
[store]
location = "~/.local/share/snapdelta"   # a directory, "sqlite://<path>", or "SCHEME://..." of a plugin backend

[stores.sqlite]                         # one table per store backend that has settings, typed by its class
busy_timeout_s = 30                     # how long a writer waits for the lock of the database

[snapshot]
jobs = 4                                # targets fetched at the same time
log_level = "info"                      # the log messages of a load kept with its snapshot: debug, info, warning, error
runner = "threads"                      # how the loads run: threads, processes, or a runner of a plugin; see parallel.md

[web]
host = "127.0.0.1"                      # another address needs a token or auth none, see api.md
port = 8765
# token = { env = "SNAPDELTA_TOKEN" }   # login of the web pages, Bearer token of the API
# tls_cert = "tls/cert.pem"             # HTTPS, with tls_key
# tls_key = "tls/key.pem"
# auth = "token"                        # "none": no login at all, on an isolated network only
# allowed_hosts = []                    # the host names of the server; with a token, empty: any
# public_url = "https://snapdelta.example.net"   # the address that a person opens, for the links of the notifiers

[sources.example-live]                  # one table per installed source, typed by its plugin
data_dir = "devices"

[inventory]                             # optional, see inventory.md; none by default
location = "inventory"                  # as the format reads it: a directory for nornir, a URL for NetBox
platforms = { junos = "junos-netconf" }

[inventories.netbox]                    # one table per inventory format that has settings, typed by its plugin
token = { env = "NETBOX_TOKEN" }

[secrets.vault]                         # one table per secret backend that has settings, typed by its plugin
token = { env = "VAULT_TOKEN" }

[notifiers.ops-chat]                    # one table per notifier, named as you like; none by default
type = "webhook"                        # the notifier plugin: webhook, command, or one of a plugin
url = { env = "OPS_CHAT_WEBHOOK" }
on = ["failure", "differences"]         # the runs to send

[profiles.change-window]                # compare profiles, see profiles.md; none by default
ignore = ["*.description"]
```

The default store is `$XDG_DATA_HOME/snapdelta` when `XDG_DATA_HOME` is set, else `~/.local/share/snapdelta`. See [stores.md](stores.md) for the two store backends and [parallel.md](parallel.md) for `jobs`.

A relative path in the file is relative to the directory of the file, and `~` is your home directory. So a project can keep its store next to its settings: `location = ".store"` or `location = "sqlite://data/snapdelta.db"`.

## Where snapdelta finds the file

snapdelta reads the first file that it finds, and only that one:

1. `--settings PATH` on the command line (`snapdelta --settings PATH ...`, `snapdelta-web --settings PATH`);
2. the environment variable `SNAPDELTA_SETTINGS`;
3. `snapdelta.toml` in the current directory;
4. `$XDG_CONFIG_HOME/snapdelta/snapdelta.toml`, else `~/.config/snapdelta/snapdelta.toml`.

When no file is found, the defaults apply. A path given with `--settings` or `SNAPDELTA_SETTINGS` must exist.

## Which value wins

For one value, a command-line option wins over an environment variable, which wins over the file, which wins over the default. Two values have an option or a variable:

| Value | Option | Environment variable |
|---|---|---|
| `store.location` | `--store` | `SNAPDELTA_STORE` |
| `snapshot.jobs` | `snapdelta snapshot --jobs N` | none |
| `web.host` | `snapdelta-web --host ADDRESS` | none |
| `web.port` | `snapdelta-web --port N` | none |
| `web.tls_cert`, `web.tls_key` | `snapdelta-web --tls-cert FILE --tls-key FILE` | none |

Everything else comes from the file.

## See the values in use

```bash
snapdelta settings
snapdelta settings --json
```

```
file: /home/me/project/snapdelta.toml
store.location = sqlite:///home/me/project/data/snapdelta.db  (file)
snapshot.jobs = 8  (file)
web.port = 8765  (default)
sources.example-live.data_dir = /home/me/project/devices  (file)
stores.sqlite.busy_timeout_s = 30.0  (default)
```

The origin of each value is `default`, `file`, `env` (with the name of the variable) or `option` (with the name of the option). Secrets are never shown: you see `env NAME` or `********`.

## Edit the settings in the web UI

The web UI has an **Admin** tab with six sections: **Store** (store information and pruning), **Settings**, **Profiles**, **Inventory**, **Models** (both read-only) and **About**.

- **Settings** shows one group per table: `store`, `snapshot`, `web`, `inventory`, then one group per installed source, store backend and inventory format that has settings. The [profiles](profiles.md) have their own tab, **Profiles**, which saves to the same file.
- A list is one item per line, and a table (`tolerance`, `counts`, `pairs`) one `KEY = VALUE` per line.
- Each field shows the value written in the file. An empty field takes its default, which shows in grey. The tag on the right is the origin of the value.
- When `SNAPDELTA_STORE` or `--store` overrides the store, the page shows the value in use and where it comes from. The form still edits the value of the file.
- The Save bar stays at the bottom of the window; after a change it shows **Unsaved changes**, and the browser asks before you leave the page. Ctrl+S (Cmd+S) saves.
- **Save settings** checks the values with the same rules as the CLI. A wrong value comes back in red with its message, and the file does not change.
- The save writes only the keys that differ from their default and removes a key that is back at its default. The rest of the file stays as it was, comments included.
- It writes the file in use. With no file in use, it creates `~/.config/snapdelta/snapdelta.toml` with mode 600.
- After a save, the web UI uses the new values at once (store, profiles, sources). A new address, port or access (`host`, `port`, `auth`, `token`, `tls_cert`, `tls_key`, `allowed_hosts`) takes effect at the next start of `snapdelta-web`, so a save cannot open a running server.

The web UI refuses a POST from another site, as for every other action. For the address and the login, see [api.md](api.md#access).

## Secrets

A secret field (a password, a token) of the web UI, a source, a store backend or an inventory format takes a plain value, or a table with one key: the secret backend, and the name of the secret in it.

```toml
[sources.junos-netconf]
password = "s3cret"                          # a plain value
token = { env = "JUNOS_NETCONF_TOKEN" }      # an environment variable
key = { file = "/run/secrets/junos-key" }    # a file, such as a Docker or Kubernetes secret
login = { vault = "net/junos#password" }     # a backend of a plugin
```

- snapdelta reads the secret from its backend each time a part needs it, never saves it, and shows only the backend and the name (`env JUNOS_NETCONF_TOKEN`) in `snapdelta settings` and the web UI. A secret that changes needs no restart.
- The built-in backends are `env`, an environment variable, and `file`, the content of a file without its last line break; the path of a `file` secret must be absolute. When the secret is missing, the part that needs it stops with a message such as `environment variable NAME is not set`.
- With a backend, the file holds no secret and can go into git. A plain value in a file that other users can read gives a warning that tells you to run `chmod 600 <file>`.
- In the web form, a secret field has a backend list, an input for the name of the secret in that backend, and an input for a new plain value. All empty keeps the current secret; **Remove** deletes it.

### Add a secret backend

A plugin adds a backend, such as HashiCorp Vault, a cloud secret manager or the system keyring, with a subclass of `snapdelta.secrets.SecretBackend` in the entry point group `snapdelta.secrets`. Its class method `read(reference, settings)` returns the secret, or raises `snapdelta.secrets.SecretError`. Its settings come from the table `[secrets.NAME]`, typed by its `settings_class`, a subclass of `snapdelta.settings.SecretBackendSettings`; a secret of these settings can come from another backend.

```python
from snapdelta.secrets import SecretBackend, SecretError
from snapdelta.settings import Secret, SecretBackendSettings


class VaultSettings(SecretBackendSettings):
    url: str = "https://vault.example.net"
    token: Secret | None = None


class VaultSecrets(SecretBackend):
    settings_class = VaultSettings

    @classmethod
    def read(cls, reference: str, settings: SecretBackendSettings) -> str:
        path, _, key = reference.partition("#")
        ...
```

```toml
# pyproject.toml of the plugin
[project.entry-points."snapdelta.secrets"]
vault = "my_plugin.secrets:VaultSecrets"
```

```toml
# snapdelta.toml
[secrets.vault]
token = { env = "VAULT_TOKEN" }
```

The names `plain`, `backend` and `reference` are the keys of the table form of a secret, so no backend can take them.

## Notifiers

After each snapshot and each saved diff, from the CLI, the web UI, the API or MCP, snapdelta sends the run to every notifier whose filter matches it. Each notifier is a `[notifiers.NAME]` table with a name of your choice, so two chat channels are two tables with the same `type`.

```toml
[web]
public_url = "https://snapdelta.example.net"   # the link of each message goes to the diff, or to the run

[notifiers.ops-chat]
type = "webhook"
url = { env = "OPS_CHAT_WEBHOOK" }             # a Slack, Teams or Mattermost incoming webhook, or your service
on = ["failure", "differences"]
kinds = ["snapshot", "diff"]
groups = ["core"]                              # only the runs on a host of these inventory groups

[notifiers.change-ticket]
type = "command"
command = ["/usr/local/bin/comment-ticket"]    # gets the run as JSON on its input
on = ["success"]
kinds = ["diff"]
```

| Key | Default | Meaning |
|---|---|---|
| `type` | required | The notifier plugin: `webhook`, `command`, or a notifier of a plugin (`snapdelta plugins`) |
| `on` | `["failure", "differences"]` | `failure`: a target or the run failed; `differences`: a diff found one; `success`: a run with no error |
| `kinds` | `["snapshot", "diff"]` | The kinds of run |
| `groups` | `[]`, every run | Only the runs on a host of one of these inventory groups |

- `webhook` sends a POST with the JSON `{"text": ..., "url": ..., "run": {...}}`: `text` is the line that a chat shows, such as `snapdelta diff of R1 from cli: differences found`. Its other keys are `headers` (a table of secrets, such as `Authorization`) and `timeout_s` (10).
- `command` runs a program with the same JSON on its input; exit code 0 means sent. Its other key is `timeout_s` (10).
- A notifier that fails never fails the run: the run records `sent` or the error of each notifier, which `snapdelta activity RUN` and the run page of **Activity** show. A wrong table (an unknown `type`, a missing key) stops snapdelta at the start, with the key.

### Add a notifier

A plugin adds a notifier, such as an email or a ticket system, with a subclass of `snapdelta.notify.Notifier` in the entry point group `snapdelta.notifiers`. Its class method `send(event, settings)` gets a `snapdelta.notify.RunEvent` (`run`, `text`, `url`, and `payload()`, the JSON of the webhook) and the settings of one table, typed by its `settings_class`, a subclass of `snapdelta.notify.NotifierSettings`; it raises an exception when the message cannot be sent.

```python
from snapdelta.notify import Notifier, NotifierSettings, RunEvent


class MailSettings(NotifierSettings):
    to: list[str]


class Mail(Notifier):
    settings_class = MailSettings

    @classmethod
    def send(cls, event: RunEvent, settings: NotifierSettings) -> None: ...
```

## Settings of a source

A plugin declares the settings of its source with a class, and snapdelta reads them from the table `[sources.NAME]`, where `NAME` is the name of the source:

```python
from pathlib import Path

from pydantic import Field
from snapdelta.settings import Secret, SourceSettings
from snapdelta.source import Source


class RouterSettings(SourceSettings):
    timeout_s: float = Field(default=30, gt=0)
    outputs: Path | None = None
    password: Secret | None = None


class RouterSource(Source):
    source = "router"
    fetches = True
    settings_class = RouterSettings

    @classmethod
    def targets(cls, settings=None) -> list[str]: ...  # `settings` is the RouterSettings that snapdelta read

    def load(self) -> None:
        settings = self.settings  # a RouterSettings
        ...  # settings.timeout_s, settings.password.value()
```

- snapdelta gives the settings to each source that it creates (`Source(..., settings=...)`), and to `targets(settings)`. A test can give its own: `RouterSource(target="r1", settings=RouterSettings(timeout_s=1))`.
- The fields are typed: a wrong type or an unknown key in the table stops the command with the key and the reason.
- A relative `Path` value in the file is relative to the file.
- A table for a source that is not installed (for example a typo, `[sources.ruoter]`) gives a warning that lists the installed sources; it is not an error, so you can uninstall a plugin and keep its table.
- The web Settings section shows and edits these fields with no extra code.

The lab is a full example: `RouterSettings` in `lab/example/src/snapdelta_lab/settings.py`, used by two sources, and the commented settings file `lab/example/snapdelta.toml`.

## Errors

A wrong value stops every command with exit code 2, and `snapdelta-web` does not start:

```
error: /home/me/bad.toml: web.port: Input should be a valid integer, unable to parse string as an integer
/home/me/bad.toml: web.pages: Extra inputs are not permitted
```

```
warning: /home/me/project/snapdelta.toml: [sources.ffr]: no source 'ffr' is installed (installed: example, example-live)
```
