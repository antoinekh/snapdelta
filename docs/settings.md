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

[web]
host = "127.0.0.1"                      # another address needs a token or auth none, see api.md
port = 8765
# token = { env = "SNAPDELTA_TOKEN" }   # login of the web pages, Bearer token of the API
# tls_cert = "tls/cert.pem"             # HTTPS, with tls_key
# tls_key = "tls/key.pem"
# auth = "token"                        # "none": no login at all, on an isolated network only
# allowed_hosts = []                    # the host names of the server; with a token, empty: any

[sources.example-live]                  # one table per installed source, typed by its plugin
data_dir = "devices"

[inventory]                             # optional, see inventory.md; none by default
location = "inventory"                  # as the format reads it: a directory for nornir, a URL for NetBox
platforms = { junos = "junos-netconf" }

[inventories.netbox]                    # one table per inventory format that has settings, typed by its plugin
token = { env = "NETBOX_TOKEN" }

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

A secret field of a source, a store backend or an inventory format (a password, a token) takes a plain value or the name of an environment variable:

```toml
[sources.junos-netconf]
password = "s3cret"                       # a plain value
token = { env = "JUNOS_NETCONF_TOKEN" }   # read from the environment when the source needs it
```

- With `{ env = "NAME" }`, the file holds no secret and can go into git. When the variable is not set, the source stops with `environment variable NAME is not set`.
- A plain value in a file that other users can read gives a warning that tells you to run `chmod 600 <file>`.
- In the web form, a secret field has two inputs: an environment variable name, or a new value. Both empty keeps the current secret; **Remove** deletes it.

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
