# TODO

Open issues found during the v0.3.0 work on `dev`, to fix later. Each item says where the problem is, what a user sees, and the fix that was decided or proposed.

## Admin UI consistency

Found in a review of the seven Admin panels in Chrome (desktop, light and dark). Decision 1 of that review is done: the groups of Settings and Profiles now have the look, the width and the sentence-case titles of the other panels, and the profile Delete button sits on the title row. The items below are still open. A partial, unreviewed attempt at items 1 to 3 is saved outside the repository as `2026-10-20-ui-task2-partial.patch`.

1. **One primary button per page.** On Profiles, **Create** is a dark primary button, like **Save profiles**, so the page has two main actions. Fix: give Create the secondary style (`.button`, not `.button primary`) in `templates/admin_settings.html`.
2. **One rule for Delete buttons.** Prune's final **Delete** is a solid red button (`button danger`), while the Delete of a profile, a diff or a snapshot is a quiet red button (`button danger quiet`). Rule to apply: deleting one item uses `button danger quiet`; the final confirmation of a bulk delete (Prune after its preview, the Delete of the bar of checked rows in the lists) uses `button danger`. Check every `danger` button with `grep -rn "danger" packages/snapdelta-web/src/snapdelta_web/templates` and align the ones that differ.
3. **A short save bar.** The save bar of Settings and Profiles shows "Saves to PATH" next to Discard and Save; a long path wraps onto two lines, and the same path is already named at the top of the page. Fix: remove the "Saves to" text from `.save-bar` in `templates/admin_settings.html`; keep the change status, Discard and Save.
4. **Prune errors in place.** On Store, **Preview** with no rule (or a bad number) opens the full "Something does not match" error page and loses the typed values. Fix: render the Store page with the message in the Prune panel (`notice warn`, as Settings does) and the typed values kept; the preview list must not show stale data. Routes in `packages/snapdelta-web/src/snapdelta_web/app.py` (`/admin/prune`), template `templates/admin.html`.
5. **Words of the UI on Store.** The fact "Outputs" and the checkbox "Outputs too" use the CLI word; the rest of the web UI says "Diffs". The field "Keep newest" does not say that it counts per target. Fix: **Diffs**, **Diffs too**, **Keep newest, per target** (and drop its placeholder "per target"). The CLI and the API keep `--outputs` and `outputs`. Update `docs/web-ui.md` and `docs/stores.md` where they name these labels.
6. **Same template pattern for the active tab.** `templates/admin.html` sets the active tab with `{% with section = "store" %}`; the other Admin templates get `section` from their route. Fix: pass `section` from the Store route (and the Prune route that renders the same page).
7. **Every section has a title.** On Inventory, the hosts table has no panel and no title, while **Groups** above it has both. Fix: put the table in a `section.panel` titled **Hosts** in `templates/admin_inventory.html`.
8. **About shows the store once.** About shows the store as `sqlite: sqlite:///...`, with the scheme twice. Fix: show the location only in `templates/admin_about.html`.
9. **A comment with history wording.** `packages/snapdelta-web/src/snapdelta_web/static/app.css` (the floated legend rule of `.settings-group`) says "no longer the rendered legend". Fix: "A floated legend is not the fieldset's rendered legend, so it sits in the box as a grid item."
10. **Screenshots.** Retake the Admin screenshots whose page changed (`docs/images/screenshots/admin-store.png`, `admin-profiles.png`, `admin-inventory.png`, `admin-about.png`) at the size of the current files (1800x1128: viewport 1440x902, device scale 1.25), with headless Chrome through Playwright; chrome-devtools adds an automation frame to its screenshots.

## Settings file: comments after a profile is deleted or created

Deleting or creating a profile in Admin > Profiles rewrites `snapdelta.toml` with tomlkit. In a file whose tables are in order, no comment of another table is lost (checked on 4000 random layouts). The values are always kept. These layouts still misplace or lose a comment:

1. **A profile split by another table.** For example `[profiles.a]`, then `[web]`, then `[profiles.a.tolerance]`, then `[profiles.b]`: deleting `a` loses the comment above `[profiles.b]` and leaves the comment of `[profiles.a.tolerance]` above `[profiles.b]`. About 30 out-of-order cases of a random test lose a comment this way.
2. **An explicit `[profiles]` header with out-of-order tables.** Deleting a profile also drops the `[profiles]` header and the comments in its body (behaviour older than v0.3.0).
3. **Create after the last profile.** When a commented table follows the profiles (`# web` above `[web]`), a new profile is written after the last profile, so `# web` ends up above the new `[profiles.NAME]` and `[web]` has no comment. Nothing is lost.

Fix idea for all three: find every part of the table by its path in the document body, move the comment run that belongs to the next table with it, and insert a new table before the comment run of the next table. Code: `save_settings` and the removal helpers in `packages/snapdelta/src/snapdelta/settings.py`; tests in `packages/snapdelta/tests/test_settings.py`.

## Smaller items

- **Profiles page:** the Delete button position depends on the 17px font of the group title; the CSS could derive it from one value. The hover border of Delete must stay inside the box after any change (check in the browser).
- **Profiles page:** an empty or blank name on **Create** shows the generic name-format message (`'': a profile name holds letters, digits, _ and - only`); a short "Give a name" message would be clearer.
- **Profiles page:** the Delete URL and the button id are built from the plain profile name. This is safe while a name holds only letters, digits, `_` and `-`; add `| urlencode` if the name rule is ever widened.
- **Snapshots page:** Enter in the Target search of the picker submits the form natively and puts `?q=...` in the URL. The server ignores it, but the URL is cluttered. Fix: suppress Enter on `[data-target-search]`.
- **CLI `snapdelta list`:** a snapshot line is `REFERENCE  ticket  source`, with empty parts dropped, so a reader cannot tell a ticket from a source when one is missing, and a ticket can hold spaces. Fix idea: `ticket: X` and `source: Y` prefixes, or a `--json` option.
- **`packages/snapdelta/src/snapdelta/ticket.py`:** the docstrings of `one_line` and `normalize_ticket` say almost the same thing, and the name `_of_text` does not say what it does.
- **Web templates:** the Export menu of the snapshot page copies the markup of the diff page; make a macro if a third page needs it.
- **Tests:** the export test of a snapshot compares the body with the same expression as the code; check a stable field of the parsed JSON instead.
- **Headless screenshots:** the theme toggle in the top bar renders as a half circle in headless Chrome.
