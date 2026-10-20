# Develop

The source of snapdelta is a uv workspace: the core, the web UI, the PostgreSQL store, and an example plugin.

```bash
uv sync
```

## The commands

```bash
make test           # uv run pytest
make lint           # ruff and pyright
make test-postgres  # the PostgreSQL store against a server in a container (docker)
make assets         # update htmx, the fonts and Swagger UI in static/vendor; HTMX_VERSION=2.0.11 pins a version
```

`make help` lists every target.

## The layout

| Path | Content |
|---|---|
| `packages/snapdelta` | Core: sources, snapshots, compare and profiles, stores, formats, inventory, settings, activity, CLI |
| `packages/snapdelta-web` | Web UI, JSON API and MCP server: FastAPI, Jinja2, htmx, MCP SDK (`snapdelta-web` and `snapdelta-mcp` commands) |
| `packages/snapdelta-postgres` | The PostgreSQL store backend, a plugin of the group `snapdelta.stores` |
| `examples/minimal-plugin` | Example plugin: models, a JSON source, a fetching source, samples, an inventory; used by the tests |
| `lab/example` | Lab: containerlab topology (FRR and SR Linux), the `frr` and `srlinux` sources, an inventory, the scenario script; its own uv project |
| `docs/` | The guides of the README |
| `skills/snapdelta` | The skill for AI agents that use the CLI |
| `scripts/` | `update-assets.sh`: download and check the files of `static/vendor`; `test-postgres.sh`: the tests of the PostgreSQL store against a server in a container |
| `Dockerfile`, `docker/` | The container image, its default settings, and a compose file with PostgreSQL |
| `.github/workflows` | `ci.yml`: tests on Python 3.13 and 3.14, and lint; `publish.yml`: publish the packages to PyPI and the image to ghcr.io when a GitHub release is published |

## The CI

`ci.yml` runs `make test` and `make lint` on Python 3.13 and 3.14, with a PostgreSQL server in a container: the tests of `snapdelta-postgres` run against it, and are skipped without it. `publish.yml` publishes the packages to PyPI and the image to ghcr.io when a GitHub release is published.

## The assets of the web UI

The web UI serves htmx, the two fonts and Swagger UI itself, from `packages/snapdelta-web/src/snapdelta_web/static/vendor`, so it works with no internet access. `make assets` downloads them from the npm registry and checks each package against the hash of the registry. The versions in use are written in `static/vendor/VERSIONS`.
