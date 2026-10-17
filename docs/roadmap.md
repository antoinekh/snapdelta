# Roadmap

The work planned for the next versions of snapdelta. Effort: **S** less than one day, **M** one to three days, **L** more. What is done is in the [changelog](../CHANGELOG.md).

| # | Idea | Effort | Status |
|---|---|---|---|
| 1 | **A queue runner and its workers, as a plugin package.** The core needs no change: the runners are a plugin group, a task is data (`LoadTask`), and `snapdelta.runners.execute()` runs it in any process. A package `snapdelta-queue` gives a runner with a job table in the PostgreSQL store (`SELECT ... FOR UPDATE SKIP LOCKED`, no broker) and its own command `snapdelta-worker`, so that the loads run where the devices are reachable and outlive the web server. Scheduled snapshots need no code: a cron job or a systemd timer runs `snapdelta snapshot --group core --label nightly`; the docs give an example. Build it when a deployment needs it. | L | Later, plugin |
| 2 | **Ready-made plugins**, so a first snapshot needs no code. Separate packages give the models and the source: `snapdelta-napalm` (many vendors through the NAPALM getters, with the same models for each vendor, so a migration compare works at once), then `snapdelta-openconfig` (gNMI or NETCONF with the OpenConfig models), `snapdelta-netbox` (an inventory format) and auth backends for LDAP and OIDC. Each package has its own dependencies, releases and tests, starts from `snapdelta plugins new`, and passes the contract tests. Start with `snapdelta-napalm`. | L | Later |
| 3 | **Secrets in the inventory.** A host of the Nornir inventory gives its password in clear text. Accept a secret of any backend there, `password: { vault: "net/r1#password" }`, read when the source needs it. | S | Later |
| 4 | **Notifiers in Admin > Settings.** The `[notifiers.NAME]` tables are named by the user, so the settings form does not show them yet: add, edit and remove a notifier there, as the profiles do. | S | Later |
| 5 | **New screenshots** of the web UI for the docs: Admin > Plugins, New diff > Many targets, a diff with its expected changes. | S | Later |
