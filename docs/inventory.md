# Inventory

An inventory is optional. Without one, snapdelta works as before: a take uses the source that you give, and the pickers list what the source can reach (`targets()`). With one, snapdelta knows your hosts, their groups and their platform:

- take a snapshot of a group: `snapdelta snapshot --group core`;
- take several vendors at once: the platform of each host chooses its source;
- give each source the data of its host: address, port, login, password and free data;
- filter the web pickers by group.

## Set the inventory

The built-in format is the Nornir [SimpleInventory](https://nornir.readthedocs.io/en/latest/tutorial/inventory.html): a directory with `hosts.yaml`, and the optional `groups.yaml` and `defaults.yaml`. snapdelta reads the files itself; Nornir does not need to be installed.

```toml
[inventory]
location = "inventory"                     # relative to the settings file
platforms = { junos = "junos-netconf", sros = "sros-netconf" }   # only when a name differs
```

```yaml
# inventory/hosts.yaml
PE1:
  hostname: 192.0.2.1
  platform: junos
  groups: [core]
  data:
    site: paris
PE2:
  hostname: 192.0.2.2
  platform: sros
  groups: [core]
```

```yaml
# inventory/groups.yaml
core:
  groups: [lab]          # a group can have parent groups
lab:
  data:
    owner: network team
```

```yaml
# inventory/defaults.yaml
port: 830
username: snapdelta
```

| Key | Meaning |
|---|---|
| `hostname`, `port` | The address of the host, for a source that connects to it |
| `username`, `password` | The login, for a source that connects to it |
| `platform` | Chooses the source: the source of the same name, or the one that `[inventory.platforms]` maps it to |
| `groups` | The groups of the host; a group can have parent groups |
| `data` | Free values for the source (site, role, ...) |

As in Nornir, a value missing on a host comes from its groups in order (each group before its parent groups), then from `defaults.yaml`; `data` is merged in the same order, the host winning. The example plugin has a complete example: `examples/minimal-plugin/inventory`.

**Passwords.** A password in the inventory goes to the source of its host and is never saved in a snapshot. When an inventory file holds a password and other users can read it, snapdelta warns: `chmod 600 inventory/*.yaml`. A source can still take its secrets from its own settings (`[sources.NAME]`, `{ env = "NAME" }`), for example a login that every host shares.

## Use it

```bash
snapdelta inventory                                  # hosts, platform, source, groups, address
snapdelta inventory --group core
snapdelta snapshot --group core --label before       # each host with the source of its platform
snapdelta snapshot --all-targets --label before      # every host of the inventory
snapdelta snapshot --target PE1 --target PE2         # names from the inventory, source by platform
snapdelta snapshot --source junos-netconf --group core   # one source for every host of the group
snapdelta diff PE1@latest --live                     # the source also gets the host of PE1
```

- Without `--source`, the platform of each host chooses its source: the installed source of the same name (platform `frr`, source `frr`), else the source that `[inventory.platforms]` maps it to (platform `junos`, source `junos-netconf`). The source must fetch its data; snapdelta lists every host that has no such source before it starts.
- `--source` still chooses one source for all the targets, and `--all-targets` with `--source` still takes the list of that source.

In the web UI:

- **Take snapshot** has the source **By platform (inventory)** and one checkbox per group.
- **New diff** has a group list next to the search of each side; it filters the targets that have snapshots.
- **Admin**, then **Inventory**, shows the groups, and for each host its platform, source, groups, address, login and data. **Admin**, then **Settings**, edits the `[inventory]` table.

## Use the host in a source

snapdelta gives each source the inventory entry of its target as `self.host` (a `snapdelta.inventory.Host`), or `None` without an inventory:

```python
def load(self) -> None:
    host = self.host
    address = host.hostname if host and host.hostname else self.target
    port = host.port if host and host.port else 830
    username = host.username if host else None
    password = host.password if host else None
    ...
```

## Other formats

A plugin adds a format with a subclass of `snapdelta.inventory.InventoryFormat`. Its class method `load(location, settings)` returns a `snapdelta.inventory.Inventory`. `location` is `[inventory] location` as written, and `settings` is the `[inventories.NAME]` table of the format, typed by its `settings_class`, a subclass of `snapdelta.settings.InventoryFormatSettings`. A secret field takes a `Secret`, as for a source ([settings.md](settings.md#secrets)).

```python
from snapdelta.inventory import Inventory, InventoryFormat
from snapdelta.settings import InventoryFormatSettings, Secret


class NetboxSettings(InventoryFormatSettings):
    token: Secret | None = None


class NetboxInventory(InventoryFormat):
    settings_class = NetboxSettings

    @classmethod
    def load(cls, location: str, settings: InventoryFormatSettings) -> Inventory:
        chosen = settings if isinstance(settings, NetboxSettings) else NetboxSettings()
        ...
```

Declare it in the entry point group `snapdelta.inventories`:

```toml
[project.entry-points."snapdelta.inventories"]
netbox = "my_plugin.inventory:NetboxInventory"
```

Then select it in the settings:

```toml
[inventory]
format = "netbox"
location = "https://netbox.example.net"

[inventories.netbox]
token = { env = "NETBOX_TOKEN" }
```

When the location of a format is a path on the machine, set `local_path = True` on its class, as the Nornir format does: the settings file then resolves a relative location against its directory. The location of another format, such as a URL, stays as written. A NetBox, Ansible or CSV inventory can come this way, with no change to snapdelta.
