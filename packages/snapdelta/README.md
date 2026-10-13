<p align="center">
  <img src="https://raw.githubusercontent.com/antoinekh/snapdelta/main/docs/images/logo-wordmark-light.svg" width="400" alt="snapdelta" />
</p>

**Snapshot your devices and services, then see exactly what changed.**

Take a snapshot of anything, then compare two snapshots. A plugin brings the [diffsync](https://github.com/networktocode/diffsync) models and a source: the way to get the data of one target, from input files, an API, NETCONF or SSH. snapdelta saves each load as a snapshot, compares two snapshots on their models, not on text, and shows what changed.

This package is the core and the `snapdelta` CLI. The web UI, the JSON API and the MCP server are in [snapdelta-web](https://pypi.org/project/snapdelta-web/).

## Install

snapdelta needs Python 3.13 or later. In your plugin project:

```bash
uv add snapdelta
```

## Use it

With a plugin source installed (here `my-source`):

```bash
snapdelta snapshot --source my-source --all-targets --label before
snapdelta snapshot --source my-source --all-targets --label after
snapdelta diff R1@before R1@after
```

## Documentation

- [Getting started](https://github.com/antoinekh/snapdelta/blob/main/docs/getting-started.md): your own project, step by step
- [Architecture](https://github.com/antoinekh/snapdelta/blob/main/docs/architecture.md): the parts and the extension points
- [Profiles](https://github.com/antoinekh/snapdelta/blob/main/docs/profiles.md): ignore, tolerance, counts, pairs
- [Inventory](https://github.com/antoinekh/snapdelta/blob/main/docs/inventory.md): the optional Nornir inventory
- [Settings](https://github.com/antoinekh/snapdelta/blob/main/docs/settings.md): every key of `snapdelta.toml`
- [Changelog](https://github.com/antoinekh/snapdelta/blob/main/CHANGELOG.md)

## License

Apache License 2.0.
