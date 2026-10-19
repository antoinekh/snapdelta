from typing import Any

import anyio
from fastapi.testclient import TestClient
from mcp import Client

from snapdelta.settings import Secret, Settings, StoreSettings, WebSettings
from snapdelta.store import SnapshotQuery
from snapdelta_web.mcp_server import create_mcp
from snapdelta_web.state import AppState


def call(store, steps) -> Any:
    """Run `steps(client)` against an in-process server on `store`."""
    server = create_mcp(AppState(Settings(store=StoreSettings(location=store.location)), store))

    async def main() -> Any:
        async with Client(server) as client:
            return await steps(client)

    return anyio.run(main)


def test_the_server_lists_its_tools_its_guide_and_its_prompts(store) -> None:
    async def steps(client: Client) -> tuple[list[str], str, list[str]]:
        tools = [tool.name for tool in (await client.list_tools()).tools]
        guide = (await client.read_resource("snapdelta://guide")).contents[0]
        prompts = [prompt.name for prompt in (await client.list_prompts()).prompts]
        return tools, getattr(guide, "text", ""), prompts

    tools, guide, prompts = call(store, steps)
    assert {"take_snapshots", "compare", "read_diff", "accept_changes", "list_runs"} <= set(tools)
    assert "## Check a change" in guide and prompts == ["check_change", "review_diff"]


def test_an_agent_checks_a_change_and_accepts_it(store) -> None:
    async def steps(client: Client) -> tuple[Any, Any, Any, Any]:
        before = await client.call_tool(
            "take_snapshots", {"targets": ["R1"], "label": "before", "source": "example-live"}
        )
        await client.call_tool("take_snapshots", {"targets": ["R1"], "label": "after", "source": "example-live"})
        compared = await client.call_tool("compare", {"left": "R1@before", "right": "R1@after", "note": "nothing"})
        name = compared.structured_content["name"]
        diff = await client.call_tool("read_diff", {"name": "20260925T110000Z-R1-vs-R1", "limit": 1})
        accepted = await client.call_tool(
            "accept_changes",
            {
                "name": "20260925T110000Z-R1-vs-R1",
                "keys": [diff.structured_content["changes"][0]["key"]],
                "comment": "planned",
            },
        )
        runs = await client.call_tool("list_runs", {})
        return (
            before.structured_content,
            (name, compared.structured_content),
            diff.structured_content,
            (
                accepted.structured_content,
                runs.structured_content,
            ),
        )

    before, (name, compared), diff, (accepted, runs) = call(store, steps)
    assert before["ok"] and before["saved"]["R1"].endswith("-before")
    assert compared["has_differences"] is False and compared["note"] == "nothing" and name
    assert diff["total"] > 1 and diff["next_offset"] == 1 and diff["changes"][0]["accepted"] is False
    assert accepted["accepted"] == 1
    origins = {run["origin"] for run in runs["result"]}
    assert origins == {"mcp"} and len(runs["result"]) == 3


def test_an_error_comes_back_as_a_tool_error(store) -> None:
    async def steps(client: Client) -> Any:
        return await client.call_tool("read_diff", {"name": "nope"})

    result = call(store, steps)
    assert result.is_error and "nope" in result.content[0].text


def test_a_page_out_of_range_comes_back_as_a_tool_error(store) -> None:
    async def steps(client: Client) -> tuple[Any, Any, Any]:
        offset = await client.call_tool("read_diff", {"name": "20260925T110000Z-R1-vs-R1", "offset": -5})
        limit = await client.call_tool("list_snapshots", {"targets": ["R1"], "limit": 0})
        above_max = await client.call_tool("list_runs", {"limit": 501})
        return offset, limit, above_max

    offset, limit, above_max = call(store, steps)
    assert offset.is_error and "offset" in offset.content[0].text
    assert limit.is_error and "limit" in limit.content[0].text
    assert above_max.is_error and "limit" in above_max.content[0].text


def test_the_web_app_serves_mcp_behind_its_token(store, make_app) -> None:
    settings = Settings(store=StoreSettings(location=store.location), web=WebSettings(token=Secret(plain="t0ken")))
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(make_app(store, settings), base_url="http://127.0.0.1") as client:
        refused = client.post("/mcp", json=request, headers=headers)
        assert refused.status_code == 401
        allowed = client.post("/mcp", json=request, headers=headers | {"Authorization": "Bearer t0ken"})
        assert allowed.status_code == 200, allowed.text
        assert "compare" in allowed.text


def test_a_browser_gets_a_page_of_the_server_and_can_try_a_tool(store, make_app) -> None:
    client = TestClient(make_app(store), base_url="http://127.0.0.1")
    redirect = client.get("/mcp", headers={"Accept": "text/html"}, follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/mcp/docs"
    page = client.get("/mcp/docs").text
    assert 'id="tool-take_snapshots"' in page and "reads the devices" in page and "snapdelta://guide" in page
    assert "http://127.0.0.1/mcp" in page and "check_change" in page
    answer = client.post("/mcp/docs/try/list_sources", data={"arguments": "{}"}).text
    assert "example-live" in answer and "Tool error" not in answer
    failed = client.post("/mcp/docs/try/read_diff", data={"arguments": '{"name": "nope"}'}).text
    assert "Tool error" in failed and "nope" in failed
    assert "not JSON" in client.post("/mcp/docs/try/read_diff", data={"arguments": "{"}).text


def test_an_agent_compares_many_targets_at_once(store) -> None:
    async def steps(client: Client) -> Any:
        compared = await client.call_tool(
            "compare_targets", {"left": "before", "right": "after", "targets": ["R1", "R9"]}
        )
        return compared.structured_content

    result = call(store, steps)
    assert result["changed"] == ["R1"] and list(result["failed"]) == ["R9"]
    assert result["diffs"][0]["diff"]["url"].startswith("/diffs/")


def test_an_agent_takes_snapshots_with_a_ticket_and_lists_them_by_ticket(store) -> None:
    async def steps(client: Client) -> Any:
        body = {"targets": ["R1"], "label": "live", "source": "example-live", "ticket": "CHG-7"}
        await client.call_tool("take_snapshots", body)
        return await client.call_tool("list_snapshots", {"targets": ["R1"], "ticket": "chg-7"})

    listed = call(store, steps)
    assert [item["label"] for item in listed.structured_content["result"]] == ["live"]


def test_an_agent_finds_the_snapshots_across_targets_by_label_and_ticket(ticketed) -> None:
    async def steps(client: Client) -> tuple[Any, Any, Any]:
        everything = await client.call_tool("list_snapshots", {})
        found = await client.call_tool("list_snapshots", {"label": "before", "ticket": "chg-2"})
        limited = await client.call_tool("list_snapshots", {"limit": 2})
        return everything, found, limited

    everything, found, limited = call(ticketed, steps)
    rows = everything.structured_content["result"]
    assert len(rows) == 6 and (rows[0]["ticket"], rows[0]["source"]) == ("CHG-1", "example")
    assert [(row["label"], row["ticket"]) for row in found.structured_content["result"]] == [("before", "CHG-2")]
    assert len(limited.structured_content["result"]) == 2


def test_an_agent_lists_the_snapshots_of_several_targets(two_targets) -> None:
    async def steps(client: Client) -> tuple[Any, Any]:
        both = await client.call_tool("list_snapshots", {"targets": ["R1", "R2"]})
        one = await client.call_tool("list_snapshots", {"targets": ["R2", "R9"]})
        return both, one

    both, one = call(two_targets, steps)
    assert {row["target"] for row in both.structured_content["result"]} == {"R1", "R2"}
    assert [row["target"] for row in one.structured_content["result"]] == ["R2"]


def test_an_agent_sets_the_ticket_and_the_note_and_finds_the_diff(ticketed) -> None:
    async def steps(client: Client) -> Any:
        compared = await client.call_tool(
            "compare", {"left": "R1@before", "right": "R1@after", "note": "r1", "ticket": "chg-1"}
        )
        name = compared.structured_content["name"]
        described = await client.call_tool("describe_diff", {"name": name, "note": "r1 uplink"})
        cleared = await client.call_tool("describe_diff", {"name": name, "note": ""})
        found = await client.call_tool("list_diffs", {"ticket": "CHG-1"})
        none = await client.call_tool("list_diffs", {"ticket": "CHG"})
        many = await client.call_tool(
            "compare_targets",
            {"left": "before", "right": "after", "targets": ["R1"], "ticket": "CHG-1", "note": "many"},
        )
        return name, compared, described, cleared, found, none, many

    name, compared, described, cleared, found, none, many = call(ticketed, steps)
    assert (compared.structured_content["ticket"], compared.structured_content["note"]) == ("CHG-1", "r1")
    assert (described.structured_content["ticket"], described.structured_content["note"]) == ("CHG-1", "r1 uplink")
    assert (cleared.structured_content["ticket"], cleared.structured_content["note"]) == ("CHG-1", None)
    assert [item["name"] for item in found.structured_content["result"]] == [name]
    assert none.structured_content["result"] == []
    diff = many.structured_content["diffs"][0]["diff"]
    assert (diff["ticket"], diff["note"]) == ("CHG-1", "many")
    assert diff["left"]["snapshot"].startswith("R1/20260926T100000Z")


def test_the_check_change_prompt_passes_the_ticket_on(store) -> None:
    async def steps(client: Client) -> tuple[str, str]:
        arguments = {"targets": "r1", "change": "VLAN 20"}
        with_ticket = await client.get_prompt("check_change", arguments | {"ticket": "CHG-7"})
        without = await client.get_prompt("check_change", arguments)
        return with_ticket.messages[0].content.text, without.messages[0].content.text

    with_ticket, without = call(store, steps)
    assert "CHG-7" in with_ticket and "take_snapshots" in with_ticket and "compare_targets" in with_ticket
    assert "CHG-7" not in without and "ticket" not in without


def test_a_ticket_or_a_note_that_is_too_long_is_refused_before_any_work(store) -> None:
    calls = [
        ("take_snapshots", {"targets": ["R1"], "source": "example-live", "ticket": "x" * 81}),
        ("compare", {"left": "R1@before", "right": "R1@after", "ticket": "x" * 81}),
        ("compare", {"left": "R1@before", "right": "R1@after", "note": "x" * 2001}),
        ("compare_targets", {"targets": ["R1"], "left": "before", "live": True, "ticket": "x" * 81}),
        ("compare_targets", {"targets": ["R1"], "left": "before", "live": True, "note": "x" * 2001}),
        ("describe_diff", {"name": "any", "ticket": "x" * 81}),
        ("describe_diff", {"name": "any", "note": "x" * 2001}),
        ("take_snapshots", {"targets": ["R1"], "source": "example-live", "ticket": f" {'x' * 81} "}),
        ("compare", {"left": "R1@before", "right": "R1@after", "ticket": f" {'x' * 81} "}),
        ("compare_targets", {"targets": ["R1"], "left": "before", "live": True, "ticket": f" {'x' * 81} "}),
        ("describe_diff", {"name": "any", "ticket": f" {'x' * 81} "}),
    ]

    async def steps(client: Client) -> list[Any]:
        return [await client.call_tool(name, arguments) for name, arguments in calls]

    before = (
        len(store.output_entries()),
        [len(store.snapshot_entries(SnapshotQuery(targets=(target,)))) for target in store.targets()],
    )
    results = call(store, steps)
    assert all(result.is_error and "String should have at most" in result.content[0].text for result in results)
    assert before == (
        len(store.output_entries()),
        [len(store.snapshot_entries(SnapshotQuery(targets=(t,)))) for t in store.targets()],
    )


def test_an_agent_gives_a_ticket_that_is_padded_but_short_after_its_normalization(store) -> None:
    ticket = f"  {'x' * 40}   {'y' * 39}  "
    padded = "x" * 40 + " " + "y" * 39

    async def steps(client: Client) -> Any:
        for label in ("before", "after"):
            await client.call_tool(
                "take_snapshots", {"targets": ["R1"], "label": label, "source": "example-live", "ticket": ticket}
            )
        compared = await client.call_tool("compare", {"left": "R1@before", "right": "R1@after", "ticket": ticket})
        many = await client.call_tool(
            "compare_targets", {"left": "before", "right": "after", "targets": ["R1"], "ticket": ticket}
        )
        name = compared.structured_content["name"]
        described = await client.call_tool("describe_diff", {"name": name, "ticket": "  CHG-1   x  "})
        removed = await client.call_tool("describe_diff", {"name": name, "ticket": ""})
        return compared, many, described, removed

    compared, many, described, removed = call(store, steps)
    assert compared.structured_content["ticket"] == padded
    assert many.structured_content["diffs"][0]["diff"]["ticket"] == padded
    assert described.structured_content["ticket"] == "CHG-1 x"
    assert removed.structured_content["ticket"] is None
