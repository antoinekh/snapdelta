import json
import re
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from snapdelta_example import ExampleSource
from snapdelta_web import views


@pytest.fixture
def client(store, make_app) -> TestClient:
    return TestClient(make_app(store), base_url="http://127.0.0.1")


def test_home_redirects_to_the_diffs(client) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/diffs"


def test_diffs_list(client, names) -> None:
    page = client.get("/diffs")
    assert page.status_code == 200
    assert f'href="/diffs/{names.same}"' in page.text
    assert "R1" in page.text
    assert "/static/vendor/htmx.min.js?v=" in page.text


def test_diff_page_opens_the_first_model_with_a_difference(client, names) -> None:
    page = client.get(f"/diffs/{names.same}")
    assert page.status_code == 200
    assert 'data-status="changed"' in page.text
    assert "uplink to R2" in page.text
    assert "interface_state" in page.text
    assert 'class="model active"' in page.text


def test_diff_page_shows_the_skipped_models(client, names) -> None:
    page = client.get(f"/diffs/{names.partial}")
    assert "Not compared, because only one side loaded them: interface_state" in page.text


def test_an_htmx_request_gets_only_the_cards(client, names) -> None:
    fragment = client.get(f"/diffs/{names.same}?model=interface_state", headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert "oper_status" in fragment.text
    assert "<html" not in fragment.text
    restore = {"HX-Request": "true", "HX-History-Restore-Request": "true"}
    assert "<html" in client.get(f"/diffs/{names.same}?model=interface_state", headers=restore).text


def test_the_filters_are_applied_by_the_server_and_kept_in_the_links(client, names) -> None:
    every = client.get(f"/diffs/{names.same}?model=vlan").text
    assert ">20</span>" in every and ">30</span>" in every
    only_right = client.get(f"/diffs/{names.same}?model=vlan&filtered=true&status=only-right").text
    assert ">30</span>" in only_right and ">20</span>" not in only_right
    assert 'href="/diffs/' + names.same + '?status=only-right&amp;filtered=true&amp;model=interface"' in only_right
    none = client.get(f"/diffs/{names.same}?model=interface&filtered=true", headers={"HX-Request": "true"}).text
    assert "No object matches the filters." in none
    searched = client.get(f"/diffs/{names.same}?model=interface&q=UPLINK", headers={"HX-Request": "true"}).text
    assert "uplink to R2" in searched and "eth2" not in searched


def test_the_cards_come_one_page_at_a_time(client, store, names, monkeypatch, make_app) -> None:
    from snapdelta_web import app as app_module

    monkeypatch.setattr(app_module, "PAGE_SIZE", 1)
    paged = TestClient(make_app(store), base_url="http://127.0.0.1")
    first = paged.get(f"/diffs/{names.same}?model=interface").text
    assert first.count('<article class="card') - first.count('class="children"') >= 1
    assert 'id="more-cards"' in first and "offset=1" in first
    second = paged.get(f"/diffs/{names.same}?model=interface&offset=1", headers={"HX-Request": "true"}).text
    assert "<html" not in second and "<article" in second


def test_model_query_selects_the_model(client, names) -> None:
    page = client.get(f"/diffs/{names.same}?model=vlan")
    assert "voip" in page.text


def test_unknown_output_is_404(client) -> None:
    page = client.get("/diffs/nope")
    assert page.status_code == 404
    assert "no output matches" in page.text


def test_snapshot_page_rows_come_by_page(store, names, monkeypatch, make_app) -> None:
    from snapdelta_web import app as app_module

    monkeypatch.setattr(app_module, "PAGE_SIZE", 2)
    paged = TestClient(make_app(store), base_url="http://127.0.0.1")
    page = paged.get(f"/snapshots/{names.before}").text
    assert "Show more (1 left)" in page and "model=interface&amp;offset=2" in page
    rows = paged.get(f"/snapshots/{names.before}?model=interface&offset=2").text
    assert "eth2" in rows and "<html" not in rows and "Show more" not in rows
    assert paged.get(f"/snapshots/{names.before}?model=nope").status_code == 404


def test_snapshot_pages(client, names) -> None:
    listing = client.get("/snapshots")
    assert listing.status_code == 200
    assert 'href="/snapshots/R1"' in listing.text
    target = client.get("/snapshots/R1")
    assert f'href="/snapshots/{names.before}"' in target.text
    assert client.get("/snapshots/R9").status_code == 404
    detail = client.get(f"/snapshots/{names.before}")
    assert detail.status_code == 200
    assert "eth0" in detail.text
    assert "interface_state" in detail.text


def test_new_diff_form_and_post(client, store, names) -> None:
    form = client.get("/new")
    assert 'hx-get="/targets?side=left"' in form.text and 'hx-get="/targets?side=right"' in form.text
    assert 'href="/new?many=true"' in form.text and 'action="/diffs/targets"' not in form.text
    response = client.post("/diffs", data={"left": "R1@latest", "right": names.before}, follow_redirects=False)
    assert response.status_code == 303
    target = response.headers["location"]
    assert target.startswith("/diffs/") and target.endswith("-R1-vs-R1")
    assert client.get(target).status_code == 200
    assert len(store.output_entries()) == 3


def test_same_compare_twice_in_one_second_saves_two_outputs(client, names, monkeypatch) -> None:
    import snapdelta.output as output_module

    fixed = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    monkeypatch.setattr(output_module, "datetime", type("Fixed", (), {"now": staticmethod(lambda tz=None: fixed)}))
    data = {"left": names.before, "right": names.after}
    first = client.post("/diffs", data=data, follow_redirects=False)
    second = client.post("/diffs", data=data, follow_redirects=False)
    assert first.headers["location"] == "/diffs/20260926T080000Z-R1-vs-R1"
    assert second.headers["location"] == "/diffs/20260926T080000Z.2-R1-vs-R1"


def test_no_common_model_is_a_400_page(client, store, names, tmp_path) -> None:
    path = tmp_path / "r2.json"
    path.write_text(json.dumps({"device": "R2", "state": {"eth0": "up"}}))
    state_only = ExampleSource(files=[path], label="state", captured_at=datetime(2026, 9, 25, 9, 0, tzinfo=UTC))
    state_only.load()
    store.save_snapshot(state_only)
    page = client.post("/diffs", data={"left": names.config, "right": "R2/20260925T090000Z-state"})
    assert page.status_code == 400
    assert "no loaded model in common" in page.text


def test_deleted_snapshot_gives_the_changed_values_only(client, store, names) -> None:
    for entry in store.snapshot_entries("R1"):
        store.delete_snapshot(entry.reference)
    page = client.get(f"/diffs/{names.same}")
    assert page.status_code == 200
    assert "Only the changed values are shown" in page.text


def test_static_files(client) -> None:
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_one_sided_cards_are_compact(client, names) -> None:
    page = client.get(f"/diffs/{names.same}")
    assert '<dl class="inline">' in page.text


def test_another_host_name_is_refused(store, make_app) -> None:
    rebound = TestClient(make_app(store), base_url="http://attacker.example")
    assert rebound.get("/diffs").status_code == 400
    assert TestClient(make_app(store), base_url="http://localhost:8765").get("/diffs").status_code == 200


@pytest.fixture
def big_client(big_store, make_app) -> TestClient:
    return TestClient(make_app(big_store), base_url="http://127.0.0.1")


def test_target_search_is_limited(big_client) -> None:
    page = big_client.get("/targets", params={"q": "dev", "side": "left"})
    assert page.status_code == 200
    assert page.text.count("data-target-option") == 20
    assert "10 more" in page.text
    assert big_client.get("/targets", params={"q": "x", "side": "middle"}).status_code == 404


def test_snapshots_of_a_target_come_50_at_a_time(big_client) -> None:
    first = big_client.get("/targets/DEV001/snapshots", params={"side": "right"})
    assert 'value="DEV001@latest" checked' in first.text
    assert first.text.count('name="right"') == 51
    assert "offset=50" in first.text
    rest = big_client.get("/targets/DEV001/snapshots", params={"side": "right", "offset": 50})
    assert rest.text.count('name="right"') == 10
    assert "offset=100" not in rest.text


def test_new_diff_preselects_a_side(client, names) -> None:
    page = client.get("/new", params={"left": names.before})
    assert f'value="{names.before}" checked' in page.text


def test_a_missing_side_is_a_400_page(client, names) -> None:
    page = client.post("/diffs", data={"left": names.before})
    assert page.status_code == 400
    assert "select a left and a right snapshot" in page.text


def test_snapshots_tab_lists_targets_then_their_snapshots(big_client) -> None:
    listing = big_client.get("/snapshots", params={"q": "dev00"})
    assert listing.status_code == 200
    assert 'href="/snapshots/DEV001"' in listing.text and "R1" not in listing.text.split("<main")[1]
    target = big_client.get("/snapshots/DEV001", params={"page": 2})
    assert target.status_code == 200
    assert target.text.count('href="/snapshots/DEV001/') == 10


def test_diffs_tab_is_filtered_and_paged(big_client, names) -> None:
    filtered = big_client.get("/diffs", params={"q": "dev005"})
    assert filtered.text.count('href="/diffs/') == 1
    second = big_client.get("/diffs", params={"page": 2})
    assert second.status_code == 200
    assert "page=1" in second.text


def test_admin_page_shows_the_store(client) -> None:
    page = client.get("/admin")
    assert page.status_code == 200
    assert "file" in page.text and "Prune" in page.text


def test_prune_previews_then_deletes(client, store) -> None:
    preview = client.post("/admin/prune", data={"keep": "1"})
    assert preview.status_code == 200
    assert "R1/20260925T110000Z-after" in preview.text and "R1/20260925T100000Z-before" in preview.text
    assert len(store.snapshot_entries("R1")) == 3
    done = client.post("/admin/prune", data={"keep": "1", "confirm": "1"}, follow_redirects=False)
    assert done.status_code == 303
    assert [entry.reference for entry in store.snapshot_entries("R1")] == ["R1/20260925T113000Z-config"]
    assert client.post("/admin/prune", data={}).status_code == 400


def test_prune_refuses_a_bad_number(client, store) -> None:
    keep = client.post("/admin/prune", data={"keep": "-1", "confirm": "1"})
    age = client.post("/admin/prune", data={"older_than": "-1", "confirm": "1"})
    too_big = client.post("/admin/prune", data={"older_than": "1000000000000", "confirm": "1"})
    assert keep.status_code == 400 and "cannot be negative" in keep.text
    assert age.status_code == 400 and "cannot be negative" in age.text
    assert too_big.status_code == 400 and "too big" in too_big.text
    assert len(store.snapshot_entries("R1")) == 3


def test_delete_buttons(client, store, names) -> None:
    page = client.get(f"/snapshots/{names.config}")
    assert f'action="/snapshots/{names.config}/delete"' in page.text
    response = client.post(f"/snapshots/{names.config}/delete", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/snapshots/R1")
    assert names.config not in [entry.reference for entry in store.snapshot_entries("R1")]
    response = client.post(f"/diffs/{names.same}/delete", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/diffs")
    assert names.same not in [entry.name for entry in store.output_entries()]


def test_a_post_from_another_site_is_refused(client, names) -> None:
    cross = client.post(f"/diffs/{names.same}/delete", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403
    foreign = client.post(f"/diffs/{names.same}/delete", headers={"Origin": "https://attacker.example"})
    assert foreign.status_code == 403
    same = client.post(
        f"/diffs/{names.same}/delete",
        headers={"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
        follow_redirects=False,
    )
    assert same.status_code == 303


def finished_job(client, response) -> str:
    """The page of a snapshot job once all its targets ended."""
    location = response.headers["location"]
    assert location.startswith("/take/jobs/")
    for _ in range(200):
        page = client.get(location).text
        if 'aria-busy="false"' in page:
            return page
        time.sleep(0.02)
    raise AssertionError(f"{location} did not end")


def test_take_a_snapshot_of_one_target(client, store) -> None:
    form = client.get("/take")
    assert form.status_code == 200
    assert 'value="example-live"' in form.text and 'value="example"' not in form.text
    suggestions = client.get("/take/targets", params={"source": "example-live", "q": "r"})
    assert 'value="R1"' in suggestions.text and 'value="R2"' in suggestions.text
    assert 'value="EDGE1"' not in suggestions.text
    taken = client.post(
        "/take", data={"source": "example-live", "target": "R2", "label": "live"}, follow_redirects=False
    )
    page = finished_job(client, taken)
    [entry] = store.snapshot_entries("R2")
    assert entry.reference.endswith("-live") and f'href="/snapshots/{entry.reference}"' in page
    assert '<span class="job-status saved">Saved</span>' in page and "Retry" not in page
    unreachable = client.post("/take", data={"source": "example-live", "target": "R9"}, follow_redirects=False)
    failed = finished_job(client, unreachable)
    assert "not reachable" in failed and "Retry the 1 failed" in failed
    retry_url = unreachable.headers["location"] + "/retry"
    assert finished_job(client, client.post(retry_url, follow_redirects=False)).count("not reachable") == 1
    assert client.post("/take", data={"source": "example", "target": "R2"}).status_code == 400


def test_lists_have_checkboxes(client, names) -> None:
    assert f'name="names" value="{names.same}"' in client.get("/diffs").text
    assert f'name="references" value="{names.before}"' in client.get("/snapshots/R1").text


def test_delete_selected_diffs(client, store, names) -> None:
    data = {"names": [names.same, names.partial], "q": "r1", "page": "1"}
    response = client.post("/diffs/delete", data=data, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/diffs?q=r1&page=1&deleted=2")
    assert store.output_entries() == []
    assert "Deleted 2." in client.get(response.headers["location"]).text
    assert client.post("/diffs/delete", data={}).status_code == 400
    assert client.post("/diffs/delete", data={"names": ["nope"]}).status_code == 404


def test_compare_two_selected_snapshots_of_a_target(client, store, names) -> None:
    page = client.get("/snapshots/R1").text
    assert 'formaction="/snapshots/R1/compare" data-no-confirm>Compare</button>' in page
    picked = {"references": [names.after, names.before]}
    done = client.post("/snapshots/R1/compare", data=picked, follow_redirects=False)
    assert done.status_code == 303
    output = store.load_output(done.headers["location"].removeprefix("/diffs/"))
    assert (output.left.label, output.right.label) == ("before", "after")
    assert client.post("/snapshots/R1/compare", data={"references": [names.before]}).status_code == 400
    assert client.post("/snapshots/R2/compare", data=picked).status_code == 404


def test_delete_selected_snapshots_of_a_target(client, store, names) -> None:
    other = client.post("/snapshots/R2/delete", data={"references": [names.before]})
    assert other.status_code == 404
    assert 'class="bulk"><span class="bulk-count"></span>' in client.get("/snapshots/R1").text
    period = {"references": [names.before], "page": "1", "since": "2026-09-25T10:30", "until": ""}
    kept = client.post("/snapshots/R1/delete", data=period, follow_redirects=False)
    assert kept.headers["location"] == "/snapshots/R1?since=2026-09-25T10%3A30&page=1&deleted=1"
    response = client.post(
        "/snapshots/R1/delete", data={"references": [names.after], "page": "1"}, follow_redirects=False
    )
    assert (response.status_code, response.headers["location"]) == (303, "/snapshots/R1?page=1&deleted=1")
    assert [entry.reference for entry in store.snapshot_entries("R1")] == [names.config]
    assert "Deleted 1." in client.get(response.headers["location"]).text
    last = client.post("/snapshots/R1/delete", data={"references": [names.config]}, follow_redirects=False)
    assert last.headers["location"] == "/snapshots?deleted=1"
    assert client.post("/snapshots/R1/delete", data={}).status_code == 400


def test_take_several_targets_at_once(client, store) -> None:
    taken = client.post(
        "/take", data={"source": "example-live", "target": ["R1", "R2"], "label": "live"}, follow_redirects=False
    )
    page = finished_job(client, taken)
    assert "<b>2</b> of <b>2</b> done: 2 saved, 0 failed" in page
    for target in ("R1", "R2"):
        reference = store.snapshot_entries(target)[0].reference
        assert f'href="/snapshots/{reference}"' in page


def test_take_all_targets_reports_the_failures(client, store) -> None:
    taken = client.post(
        "/take", data={"source": "example-live", "all_targets": "true", "target": ["R9"]}, follow_redirects=False
    )
    assert "R9: not reachable" in finished_job(client, taken)
    assert sorted(store.targets()) == ["EDGE1", "R1", "R2"]


def test_take_needs_a_target(client) -> None:
    response = client.post("/take", data={"source": "example-live"})
    assert response.status_code == 400 and "select a target" in response.text


def test_target_matches_are_checkboxes_without_the_selected_ones(client) -> None:
    matches = client.get("/take/targets", params={"source": "example-live", "q": "r", "target": ["R1"]})
    assert 'name="target" value="R2"' in matches.text and 'value="R1"' not in matches.text
    typed = client.get("/take/targets", params={"source": "example-live", "q": "NEW1"})
    assert 'name="target" value="NEW1"' in typed.text
    partial = client.get("/take/targets", params={"source": "example-live", "q": "R"})
    assert 'value="R"' not in partial.text


def test_new_diff_lists_the_models_in_common(client, names) -> None:
    models = client.get("/new/models", params={"left": names.before, "right": names.config})
    assert models.status_code == 200
    assert 'name="models" value="interface" checked' in models.text
    assert 'name="models" value="vlan" checked' in models.text
    assert "Not compared, because only one side loaded them: interface_state" in models.text
    assert client.get("/new/models", params={"left": names.before}).text.strip() == ""


def test_new_diff_warns_when_no_model_is_in_common(client, store, names, tmp_path) -> None:
    path = tmp_path / "r2.json"
    path.write_text(json.dumps({"device": "R2", "state": {"eth0": "up"}}))
    state_only = ExampleSource(files=[path], label="state", captured_at=datetime(2026, 9, 25, 9, 0, tzinfo=UTC))
    state_only.load()
    store.save_snapshot(state_only)
    models = client.get("/new/models", params={"left": names.config, "right": "R2/20260925T090000Z-state"})
    assert "No model in common" in models.text
    assert "interface, address, vlan" in models.text and "interface_state" in models.text


def test_diff_of_selected_models(client, store, names) -> None:
    data = {"left": names.before, "right": names.after, "models": ["vlan"]}
    response = client.post("/diffs", data=data, follow_redirects=False)
    assert response.status_code == 303
    name = response.headers["location"].removeprefix("/diffs/")
    assert store.load_output(name).compared == ["vlan"]
    assert "Not selected: interface" in client.get(f"/diffs/{name}").text


def test_diff_needs_one_selected_model(client, names) -> None:
    page = client.post("/diffs", data={"left": names.before, "right": names.after, "pick_models": "true"})
    assert page.status_code == 400 and "select at least one model" in page.text


def test_an_unknown_page_is_a_404_page(client) -> None:
    page = client.get("/no/such/page")
    assert page.status_code == 404
    assert 'class="not-found"' in page.text and "/no/such/page" in page.text
    assert 'href="/diffs"' in page.text


def test_an_unknown_snapshot_is_a_404_page_with_the_reason(client) -> None:
    page = client.get("/snapshots/R9/20260101T000000Z")
    assert page.status_code == 404
    assert 'class="not-found"' in page.text and "R9" in page.text


def test_an_unexpected_error_is_a_500_page_without_details(store, monkeypatch, make_app) -> None:
    app = make_app(store)

    def broken(query: str = "") -> list:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(store, "output_entries", broken)
    page = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False).get("/diffs")
    assert page.status_code == 500
    assert 'class="error-page"' in page.text and "secret detail" not in page.text


def test_favicon(client) -> None:
    assert 'rel="icon" href="/static/favicon.svg?v=' in client.get("/diffs").text
    assert client.get("/static/favicon.svg").headers["content-type"].startswith("image/svg+xml")
    redirect = client.get("/favicon.ico", follow_redirects=False)
    assert redirect.headers["location"] == "/static/favicon.svg"


def test_static_links_change_with_the_file_content(client) -> None:
    page = client.get("/diffs").text
    assert re.search(r'app\.css\?v=[0-9a-f]{12}"', page) and re.search(r'app\.js\?v=[0-9a-f]{12}"', page)


def test_the_lists_come_one_page_at_a_time(big_store, monkeypatch, make_app) -> None:
    from snapdelta_web import app as app_module

    monkeypatch.setattr(app_module, "PAGE_SIZE", 7)
    client = TestClient(make_app(big_store), base_url="http://127.0.0.1")
    page = client.get("/snapshots")
    assert page.text.count('class="row-link mono" href="/snapshots/DEV') == 7


def test_admin_has_store_and_settings_sections(client) -> None:
    page = client.get("/admin")
    assert 'href="/admin/settings"' in page.text and '<a aria-current="page" href="/admin">' in page.text


def test_the_target_search_can_be_refreshed_after_an_uncheck(client) -> None:
    page = client.get("/take").text
    assert 'hx-trigger="input changed delay:200ms, focus once, refresh"' in page


def test_export_a_diff_in_each_format(client, names) -> None:
    page = client.get(f"/diffs/{names.same}").text
    assert f'href="/diffs/{names.same}/export/markdown"' in page
    markdown = client.get(f"/diffs/{names.same}/export/markdown")
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.headers["content-disposition"] == f'attachment; filename="{names.same}.md"'
    assert "## interface" in markdown.text
    assert client.get(f"/diffs/{names.same}/export/csv").text.startswith("model,object,parents")
    assert client.get(f"/diffs/{names.same}/export/pdf").status_code == 400
    assert client.get("/diffs/nope/export/csv").status_code == 404


def test_compare_a_snapshot_with_its_target_now(client, store) -> None:
    taken = client.post("/take", data={"source": "example-live", "target": ["R2"]}, follow_redirects=False)
    finished_job(client, taken)
    reference = store.snapshot_entries("R2")[0].reference
    page = client.get(f"/snapshots/{reference}").text
    assert 'action="/diffs/live"' in page and "Compare with live" in page
    response = client.post("/diffs/live", data={"left": reference}, follow_redirects=False)
    assert response.status_code == 303
    output = store.load_output(response.headers["location"].removeprefix("/diffs/"))
    assert output.left.snapshot == reference and output.right.label == "live"
    assert not output.has_differences()


def test_a_snapshot_of_files_has_no_live_compare(client, names) -> None:
    assert "Compare with live" not in client.get(f"/snapshots/{names.before}").text
    assert client.post("/diffs/live", data={"left": names.before}).status_code == 400


def test_a_diff_with_a_profile_marks_the_accepted_values(store, names, make_app) -> None:
    from snapdelta.settings import Profile, Settings, StoreSettings

    profiles = {
        "quiet": Profile(description="No descriptions", ignore=["interface.description"], counts={"vlan": "50%"})
    }
    settings = Settings(store=StoreSettings(location=store.location), profiles=profiles)
    client = TestClient(make_app(store, settings), base_url="http://127.0.0.1")
    form = client.get("/new").text
    assert '<select name="profile">' in form and '<option value="quiet">quiet: No descriptions</option>' in form
    data = {"left": names.before, "right": names.after, "profile": "quiet"}
    name = client.post("/diffs", data=data, follow_redirects=False).headers["location"].removeprefix("/diffs/")
    output = store.load_output(name)
    assert output.profile is not None and output.profile.name == "quiet"
    page = client.get(f"/diffs/{name}?model=interface").text
    assert "profile quiet" in page and '<span class="muted">No descriptions</span>' in page
    assert "vlan 2 → 3, limit 50%, 1 hidden" in page
    assert '<span class="tag">ignored</span>' in page
    bad = client.post("/diffs", data=data | {"profile": "nope"})
    assert bad.status_code == 400 and "no profile &#39;nope&#39;" in bad.text


def test_the_models_tab_lists_the_models_of_each_set_of_sources(client) -> None:
    page = client.get("/admin/models")
    assert page.status_code == 200
    assert 'aria-current="page" href="/admin/models"' in page.text
    assert '<b class="mono">example</b>' in page.text and '<b class="mono">example-live</b>' in page.text
    assert 'id="model-example-address"' in page.text and "interface › </span>address" in page.text
    assert '<span class="mono">mtu</span> <span class="field-type mono">int | None</span>' in page.text
    assert 'href="/admin/models"' in client.get("/admin/profiles").text


def test_the_plugins_tab_shows_each_extension_point_with_its_problems(client) -> None:
    from snapdelta.plugins import register_plugin
    from snapdelta.render import RENDERERS, Renderer

    register_plugin(RENDERERS, "crash", Renderer("text/plain", "txt", lambda output: str(1 / 0)))
    page = client.get("/admin/plugins")
    assert page.status_code == 200 and 'aria-current="page" href="/admin/plugins"' in page.text
    assert "<th>In use</th>" in page.text and 'aria-label="In use: the store">✓</span>' in page.text
    for group in ("snapdelta.sources", "snapdelta.stores", "snapdelta.renderers", "snapdelta.inventories"):
        assert f'<span class="tag mono">{group}</span>' in page.text
    assert 'id="plugin-stores-sqlite"' in page.text and 'id="plugin-sources-example-live"' in page.text
    assert "render() fails on a sample diff: ZeroDivisionError" in page.text


def test_the_chips_count_the_selected_model(client, names, store) -> None:
    output = store.load_output(names.same)
    vlan = dict(views.model_deltas(output))["vlan"]
    page = client.get(f"/diffs/{names.same}?model=vlan").text
    assert f'<b class="num" id="chip-only-right">{vlan.only_right}</b>' in page
    assert f"{output.summary['no-change']} unchanged" in page
    fragment = client.get(f"/diffs/{names.same}?model=vlan", headers={"HX-Request": "true"}).text
    assert f'<b class="num" id="chip-changed" hx-swap-oob="true">{vlan.changed}</b>' in fragment
    more = client.get(f"/diffs/{names.same}?model=vlan&offset=50", headers={"HX-Request": "true"}).text
    assert "hx-swap-oob" not in more


def inventory_client(store, make_app) -> TestClient:
    from pathlib import Path

    from snapdelta.settings import InventorySettings, Settings, StoreSettings

    location = Path(__file__).resolve().parents[3] / "examples" / "minimal-plugin" / "inventory"
    inventory = InventorySettings(location=str(location), platforms={"example": "example-live"})
    settings = Settings(store=StoreSettings(location=store.location), inventory=inventory)
    return TestClient(make_app(store, settings), base_url="http://127.0.0.1")


def test_take_a_group_by_platform(store, make_app) -> None:
    client = inventory_client(store, make_app)
    form = client.get("/take").text
    assert '<option value="">By platform (inventory)</option>' in form
    assert '<input type="checkbox" name="group" value="core">' in form
    targets = client.get("/take/targets", params={"source": "", "q": "r"}).text
    assert 'value="R1"' in targets and 'value="R2"' in targets
    taken = client.post("/take", data={"source": "", "group": ["core"], "label": "inv"}, follow_redirects=False)
    assert "2 saved, 0 failed" in finished_job(client, taken)
    assert [entry.label for entry in store.snapshot_entries("R2")] == ["inv"]


def test_new_diff_filters_the_targets_by_group(store, make_app) -> None:
    client = inventory_client(store, make_app)
    assert '<option value="">All groups</option><option>core</option>' in client.get("/new").text
    many = client.get("/new", params={"many": "true"}).text
    assert 'value="core"> core</label>' in many and 'data-members="core" hidden>R1, R2</p>' in many
    edge = client.get("/targets", params={"side": "left", "group": "edge"}).text
    assert "No target matches." in edge
    core = client.get("/targets", params={"side": "left", "group": "core"}).text
    assert ">R1</button>" in core


def test_the_inventory_tab(store, client, make_app) -> None:
    page = inventory_client(store, make_app).get("/admin/inventory").text
    assert "Inventory in use:" in page and '<td class="mono strong">EDGE1</td>' in page
    assert "192.0.2.10:2222" in page and '<span class="tag">example-live</span>' in page
    assert "No inventory." in client.get("/admin/inventory").text


def test_a_diff_gets_a_title_at_the_compare_and_later(client, store, names) -> None:
    data = {"left": names.before, "right": names.after, "title": "Change 42", "description": "New uplink"}
    name = client.post("/diffs", data=data, follow_redirects=False).headers["location"].removeprefix("/diffs/")
    page = client.get(f"/diffs/{name}").text
    assert '<h1 class="diff-title">Change 42</h1>' in page and "New uplink" in page
    assert ">Change 42</a>" in client.get("/diffs").text
    client.post(f"/diffs/{name}/describe", data={"title": "Change 43", "description": ""})
    assert (store.load_output(name).title, store.load_output(name).description) == ("Change 43", None)
    assert ">Change 43</a>" in client.get("/diffs").text


def test_every_page_links_the_project_and_the_about_tab_shows_the_versions(client) -> None:
    page = client.get("/diffs").text
    assert 'href="https://github.com/antoinekh/snapdelta"' in page and ">snapdelta 0.2.0</a>" in page
    about = client.get("/admin/about").text
    assert "snapdelta-web" in about and "diffsync" in about and "Python" in about
    assert "snapdelta-example" in about and "source example, source example-live" in about
    assert "Apache 2.0" in about


def test_new_diff_selects_a_snapshot_by_its_label(client, names) -> None:
    page = client.get("/new", params={"left": "R1@before", "right": "R1@latest"}).text
    assert f'value="{names.before}" checked' in page and 'value="R1@latest" checked' in page


def test_pages_load_nothing_from_another_site(client) -> None:
    import re

    for path in ("/diffs", "/api/v1/docs"):
        page = client.get(path).text
        assets = re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', page)
        assets += re.findall(r'url: ?"([^"]+)"', page)
        assert assets and all(url.startswith("/") for url in assets), (path, assets)
        for url in assets:
            assert client.get(url).status_code == 200, url


def test_fields_that_are_never_compared_show_in_the_models_and_the_snapshots(client, names) -> None:
    models = client.get("/admin/models").text
    assert "Not compared" in models and "ifindex" in models
    snapshot = client.get(f"/snapshots/{names.before}").text
    assert '<th class="context" title="Kept in the snapshot, never compared">ifindex</th>' in snapshot
    assert '<td class="mono context">2</td>' in snapshot
    diff = client.get(f"/diffs/{names.same}?model=interface").text
    assert "not compared" in diff


def test_the_target_page_loads_its_timeline(client, names) -> None:
    assert 'hx-get="/targets/R1/timeline"' in client.get("/snapshots/R1").text
    part = client.get("/targets/R1/timeline", params={"profile": ""}).text
    assert part.count('class="timeline-slot"') == 3 and part.count("data-left=") == 2
    assert part.count('<b class="timeline-day">25 Sep</b>') == 1 and 'data-caption="before 10:00"' in part
    assert f'data-left="{names.before}" data-right="{names.after}"' in part
    assert 'action="/diffs"' in part and "data-timeline-compare disabled" in part


def test_the_target_page_filters_its_snapshots_by_period(client, names) -> None:
    period = {"since": "2026-09-25T10:30", "until": "2026-09-25T11:00"}
    page = client.get("/snapshots/R1", params=period).text
    assert f'value="{names.after}"' in page
    assert f'value="{names.before}"' not in page and f'value="{names.config}"' not in page
    assert 'hx-get="/targets/R1/timeline?since=2026-09-25T10%3A30&amp;until=2026-09-25T11%3A00"' in page
    assert 'value="2026-09-25T10:30"' in page and 'href="/snapshots/R1">Clear</a>' in page
    part = client.get("/targets/R1/timeline", params={"since": "2026-09-25T10:30"}).text
    assert part.count('class="timeline-slot"') == 2
    assert "No snapshot in this period." in client.get("/snapshots/R1", params={"since": "2030-01-01T00:00"}).text
    assert client.get("/snapshots/R1", params={"since": "soon"}).status_code == 400


def test_accept_a_change_on_the_diff_page(client, names, store) -> None:
    page = client.get(f"/diffs/{names.same}?model=interface").text
    assert "data-reviewer" in page
    assert '0</b> of <b class="mono">' in page and 'name="key" value="interface:eth0"' in page
    headers = {"HX-Request": "true"}
    form = {"key": "interface:eth0", "by": "alice", "comment": "planned"}
    part = client.post(f"/diffs/{names.same}/reviews", data=form, headers=headers).text
    assert 'class="review done"' in part and "by <b>alice</b>" in part and 'hx-swap-oob="true"' in part
    assert store.load_output(names.same).reviews["interface:eth0"].comment == "planned"
    assert "✓ 1/" in client.get("/diffs").text
    back = client.post(f"/diffs/{names.same}/reviews", data={"key": "interface:eth0", "undo": "true"})
    assert back.status_code == 200 and store.load_output(names.same).reviews == {}


def test_a_card_has_a_link_and_the_diff_an_api_call(client, names) -> None:
    page = client.get(f"/diffs/{names.same}?model=interface").text
    assert 'id="card-interface:eth0"' in page and 'data-copy-link="eth0"' in page
    assert f'data-copy-api="/api/v1/diffs/{names.same}?format=json" data-token=""' in page
    linked = client.get(f"/diffs/{names.same}", params={"model": "interface", "q": "eth0"}).text
    assert 'id="card-interface:eth0"' in linked and 'id="card-interface:eth3"' not in linked


def test_the_palette_finds_targets_diffs_and_pages(client, names) -> None:
    empty = client.get("/palette").text
    assert "Diffs" in empty and 'href="/admin/profiles"' in empty and "Targets" not in empty
    found = client.get("/palette", params={"q": "r1"}).text
    assert 'href="/snapshots/R1"' in found and f'href="/diffs/{names.same}"' in found
    assert "Nothing matches zzz." in client.get("/palette", params={"q": "zzz"}).text
    assert "data-palette-open" in client.get("/diffs").text


def test_every_page_has_the_shortcuts_window(client) -> None:
    page = client.get("/diffs").text
    assert '<dialog id="shortcuts"' in page and "<kbd>a</kbd></td><td>Accept the object, or undo" in page
    assert "data-shortcuts-open" in page


def test_new_diff_keeps_the_title_and_tells_a_missing_label(client, names) -> None:
    page = client.get(
        "/new", params={"left": "R1@before", "right": "R1@nope", "title": "Change 7", "profile": "none"}
    ).text
    assert f'value="{names.before}" checked' in page and 'value="R1@latest" checked' in page
    assert "No snapshot of R1 is labelled nope: the newest snapshot is selected." in page
    assert 'value="Change 7"' in page and "data-swap-sides" in page and 'data-preset-labels="before after"' in page


def test_the_diffs_of_one_change_are_grouped(client, names, store) -> None:
    for name, title in ((names.same, "CHG-9: first"), (names.partial, "CHG-9: second")):
        store.describe_output(name, title, None, None)
    page = client.get("/diffs").text
    assert page.count('<tr class="group-day">') == 1 and '<span class="group-name">CHG-9</span>' in page
    assert '<a class="row-link" href="/diffs/' in page and ">first</a>" in page and ">second</a>" in page


def wait_for_runs(store, count: int = 1) -> None:
    """The take job runs in a thread of the server: wait until it records its run."""
    import time

    deadline = time.monotonic() + 10
    while len(store.runs()) < count and time.monotonic() < deadline:
        time.sleep(0.02)


def test_the_activity_lists_the_runs_and_shows_their_log(client, store, monkeypatch) -> None:
    from snapdelta.activity import configure_logging
    from snapdelta_example.live import DEVICES as devices

    configure_logging("info")
    page = client.post("/take", data={"source": "example-live", "target": ["R1"], "label": "logged"})
    assert page.status_code == 200
    wait_for_runs(store)
    listed = client.get("/activity").text
    assert '<span class="tag">snapshot</span>' in listed and '<span class="run-status ok">OK</span>' in listed
    run = store.runs()[0]
    ok = client.get("/activity", params={"status": "ok"}).text
    assert '<span class="run-status ok">OK</span>' in ok and 'href="/activity?kind=snapshot&amp;status=ok"' in ok
    assert "No run matches." in client.get("/activity", params={"status": "failed"}).text
    assert "No run matches." in client.get("/activity", params={"since": "2099-01-01T00:00"}).text
    detail = client.get(f"/activity/{run.id}").text
    assert f"read R1.json from {devices}" in detail and f'href="/snapshots/{run.saved[0]}"' in detail
    assert "No message at warning or above" in client.get(f"/activity/{run.id}", params={"level": "warning"}).text
    snapshot = client.get(f"/snapshots/{run.saved[0]}").text
    assert "Load log" in snapshot and "read R1.json" in snapshot
    listed = client.get("/api/v1/runs").json()[0]
    assert listed["log_lines"] == 1 and listed["notified"] == {} and listed["has_differences"] is None
    assert client.get(f"/api/v1/runs/{run.id}").json()["log"][0]["target"] == "R1"


def test_the_new_diff_page_compares_many_targets(client, store) -> None:
    page = client.get("/new", params={"many": "true"}).text
    assert 'action="/diffs/targets"' in page and 'name="live" value="true"' in page and 'action="/diffs"' not in page
    data = {"left": "before", "right": "after", "live": "false", "title": "CHG-9", "description": "Swap of the core"}
    result = client.post("/diffs/targets", data=data)
    assert result.status_code == 200
    assert "<h1>CHG-9</h1>" in result.text and "1 changed, 0 unchanged, 0 failed" in result.text
    newest = max(store.output_entries(), key=lambda entry: entry.created_at)
    assert store.load_output(newest.name).description == "Swap of the core"
    assert 'href="/diffs/' in result.text
    refused = client.post("/diffs/targets", data={"left": "nope", "right": "after"})
    assert refused.status_code == 400 and "give targets, groups or all_targets" in refused.text
