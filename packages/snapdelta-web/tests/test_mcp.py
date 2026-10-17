from typing import Any

import anyio
from fastapi.testclient import TestClient
from mcp import Client

from snapdelta.settings import Secret, Settings, StoreSettings, WebSettings
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
        compared = await client.call_tool(
            "compare", {"left": "R1@before", "right": "R1@after", "title": "CHG-7: nothing"}
        )
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
    assert compared["has_differences"] is False and compared["title"] == "CHG-7: nothing" and name
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
        limit = await client.call_tool("list_snapshots", {"target": "R1", "limit": 0})
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
