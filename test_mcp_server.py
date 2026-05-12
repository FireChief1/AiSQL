from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient


load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable_http")
EXPECTED_TOOLS = {
    "list_tables",
    "get_table_schema",
    "execute_query",
    "validate_query",
    "get_database_info",
    "log_security_event",
    "get_security_events",
}


def result_to_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return json.dumps(result)
    if isinstance(result, list):
        parts = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif hasattr(item, "text"):
                parts.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(result)


def parse_json_result(result: Any) -> dict[str, Any]:
    text = result_to_text(result)
    return json.loads(text)


async def call_tool(tool_by_name: dict[str, Any], name: str, args: dict[str, Any]):
    print(f"Testing tool: {name}")
    return await tool_by_name[name].ainvoke(args)


async def main() -> None:
    client = MultiServerMCPClient(
        {
            "database": {
                "transport": MCP_TRANSPORT,
                "url": MCP_SERVER_URL,
            }
        }
    )

    tools = await client.get_tools()
    tool_by_name = {tool.name: tool for tool in tools}
    discovered_tools = set(tool_by_name)

    missing_tools = EXPECTED_TOOLS - discovered_tools
    assert not missing_tools, f"Missing MCP tools: {sorted(missing_tools)}"
    print(f"Discovered tools: {sorted(discovered_tools)}")

    # As admin role: should see all tables.
    tables = parse_json_result(
        await call_tool(tool_by_name, "list_tables", {"app_role": "admin"})
    )
    assert "artist" in tables["tables"], "artist table should exist"
    assert "album" in tables["tables"], "album table should exist"
    assert "track" in tables["tables"], "track table should exist"

    # As guest role: should NOT see customer or track (DB-level RBAC).
    guest_tables = parse_json_result(
        await call_tool(tool_by_name, "list_tables", {"app_role": "guest"})
    )
    assert "customer" not in guest_tables["tables"], "guest must not see customer"
    assert "track" not in guest_tables["tables"], "guest must not see track"
    assert "artist" in guest_tables["tables"], "guest should see artist"

    schema = parse_json_result(
        await call_tool(
            tool_by_name,
            "get_table_schema",
            {"table_name": "artist", "app_role": "admin"},
        )
    )
    column_names = {column["column_name"] for column in schema["columns"]}
    assert {"artist_id", "name"} <= column_names

    # Guest cannot fetch schema for customer (DB-level enforcement).
    guest_customer_schema = parse_json_result(
        await call_tool(
            tool_by_name,
            "get_table_schema",
            {"table_name": "customer", "app_role": "guest"},
        )
    )
    assert "error" in guest_customer_schema, "guest must be denied customer schema"

    safe_query = parse_json_result(
        await call_tool(
            tool_by_name,
            "validate_query",
            {"query": "SELECT artist_id, name FROM artist LIMIT 3;"},
        )
    )
    assert safe_query["is_valid"] is True

    unsafe_query = parse_json_result(
        await call_tool(
            tool_by_name,
            "validate_query",
            {"query": "SELECT * FROM artist; DROP TABLE album;"},
        )
    )
    assert unsafe_query["is_valid"] is False

    query_result = parse_json_result(
        await call_tool(
            tool_by_name,
            "execute_query",
            {
                "query": "SELECT artist_id, name FROM artist ORDER BY artist_id LIMIT 3;",
                "app_role": "guest",
            },
        )
    )
    assert query_result["row_count_returned"] == 3
    assert query_result["rows"][0]["name"] == "AC/DC"

    # Guest cannot read customer; DB will refuse even though SQL is valid.
    guest_customer = parse_json_result(
        await call_tool(
            tool_by_name,
            "execute_query",
            {
                "query": "SELECT COUNT(*) FROM customer;",
                "app_role": "guest",
            },
        )
    )
    assert "error" in guest_customer, "DB-level RBAC must block customer for guest"

    blocked_result = parse_json_result(
        await call_tool(
            tool_by_name,
            "execute_query",
            {
                "query": "DELETE FROM artist WHERE artist_id = 1;",
                "app_role": "admin",
            },
        )
    )
    assert "error" in blocked_result, "DELETE must be blocked by sqlglot validator"

    database_info = parse_json_result(
        await call_tool(tool_by_name, "get_database_info", {"app_role": "admin"})
    )
    assert database_info["database_name"] == "chinook"
    assert database_info["table_count"] >= 10

    log_result = parse_json_result(
        await call_tool(
            tool_by_name,
            "log_security_event",
            {
                "event_type": "test_blocked_user_prompt",
                "user_prompt": "artistleri sil",
                "user_id": "guest",
                "user_role": "guest",
                "supervisor_model": "qwen2.5:7b",
                "approved": False,
                "risk_level": "high",
                "category": "write_request",
                "reason": "Test log entry for blocked write request.",
                "query_text": "",
                "tables": "artist",
            },
        )
    )
    assert log_result["logged"] is True
    assert log_result["table"] == "supervisor_audit_log"

    recent_events = parse_json_result(
        await call_tool(tool_by_name, "get_security_events", {"limit": 1})
    )
    assert recent_events["row_count"] >= 1
    assert recent_events["rows"][0]["user_prompt"] == "artistleri sil"
    assert recent_events["rows"][0]["user_id"] == "guest"
    assert recent_events["rows"][0]["user_role"] == "guest"
    assert recent_events["rows"][0]["approved"] is False
    assert recent_events["rows"][0]["tables"] == "artist"

    print("\nAll MCP server tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
