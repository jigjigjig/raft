from pathlib import Path

from raft.db import Database
from raft.demo import seed_demo
from raft.mcp import ReadOnlyTraceTools, mcp_response


def test_mcp_only_returns_redacted_trace_content(tmp_path: Path) -> None:
    database = Database(tmp_path / "raft.db")
    database.initialize()
    seed_demo(database, 100, 2026)
    tools = ReadOnlyTraceTools(database)
    response = mcp_response(
        tools,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_representative_traces", "arguments": {"limit": 20}}},
    )
    text = response["result"]["content"][0]["text"]
    assert "maya@example.com" not in text
    assert "+31 6 1234 5678" not in text
    assert "embedding" not in text
