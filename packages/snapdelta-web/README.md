<p align="center">
  <img src="https://raw.githubusercontent.com/antoinekh/snapdelta/main/docs/images/logo-wordmark-light.svg" width="400" alt="snapdelta" />
</p>

**Web UI, JSON API and MCP server for [snapdelta](https://pypi.org/project/snapdelta/): snapshot your devices and services, then see exactly what changed.**

![A diff in the web UI](https://raw.githubusercontent.com/antoinekh/snapdelta/main/docs/images/screenshots/diff.png)

## Install

snapdelta needs Python 3.13 or later. In your plugin project:

```bash
uv add snapdelta snapdelta-web
```

## Use it

| Command | What it does |
|---|---|
| `snapdelta-web` | Serve the web UI, the JSON API under `/api/v1` and the MCP server under `/mcp` |
| `snapdelta-mcp` | Serve the MCP tools to an AI agent over stdio |

```bash
snapdelta-web --store ./store
claude mcp add snapdelta -- snapdelta-mcp --settings /path/to/snapdelta.toml
```

The web UI serves htmx, its fonts and the API docs page itself, so it works with no internet access.

## Documentation

- [Web UI](https://github.com/antoinekh/snapdelta/blob/main/docs/web-ui.md): the pages, with screenshots
- [API](https://github.com/antoinekh/snapdelta/blob/main/docs/api.md): the JSON API, the token and the network access
- [MCP](https://github.com/antoinekh/snapdelta/blob/main/docs/mcp.md): the MCP server and the skill for AI agents
- [Getting started](https://github.com/antoinekh/snapdelta/blob/main/docs/getting-started.md): your own project, step by step
- [Changelog](https://github.com/antoinekh/snapdelta/blob/main/CHANGELOG.md)

## License

Apache License 2.0.
