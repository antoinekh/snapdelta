# Roadmap

The work planned for the next versions of snapdelta. Effort: **S** less than one day, **M** one to three days, **L** more.

| # | Idea | Effort | Status |
|---|---|---|---|
| 1 | **Scheduled snapshots: a queue and workers.** Snapshots that run with no person: every night, or before and after each change window. The web UI and the API add jobs to a job table (SQLite); a `snapdelta worker` command takes them and runs them, and `snapdelta schedule` (or cron) adds the jobs at fixed times. No external broker: one more process at most. A snapshot of the web UI already runs in the background of the web server, and Activity keeps every run, so the table is needed only for jobs that must outlive the web server. | M | Later |
| 2 | **PostgreSQL store**, next to the file and SQLite stores: a third backend of the `snapdelta.stores` entry point group, with the tables of the SQLite store and `psycopg` 3 as an optional dependency (`snapdelta[postgres]`). | M | Later |
| 3 | **Remove `diff --source --file`.** Load the files with `snapshot`, then compare two snapshots: one way to give the right side. | S | To decide |
