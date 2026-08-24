from __future__ import annotations

import json
from typing import Any

from raft.db import Database


TOOL_DEFINITIONS = [
    {
        "name": "describe_dataset",
        "description": "Describe the redacted Raft trace dataset and its capture limits.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_shape_fields",
        "description": "List deterministic trace-shape fields that can be aggregated without model judgment.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_aspects",
        "description": "List reusable content aspects already evaluated over traces.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_representative_traces",
        "description": "Read a small redacted sample for planning. Never returns raw or unredacted text.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_trace_rows",
        "description": "Read selected redacted trace rows for an evidence set.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 50,
                }
            },
            "required": ["trace_ids"],
            "additionalProperties": False,
        },
    },
]


class ReadOnlyTraceTools:
    def __init__(self, db: Database):
        self.db = db

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        if name == "describe_dataset":
            counts = self.db.fetch_all(
                "SELECT outcome,COUNT(*) AS count FROM traces GROUP BY outcome ORDER BY count DESC"
            )
            return {
                "trace_count": self.db.trace_count(),
                "content_policy": "redacted-only",
                "capture_completeness": "Synthetic demo traces; missing values remain unknown.",
                "outcomes": counts,
            }
        if name == "list_shape_fields":
            return {
                "fields": [
                    "app",
                    "started_at",
                    "duration_ms",
                    "model",
                    "provider",
                    "status",
                    "capture_completeness",
                    "cost_usd",
                    "tokens_input",
                    "tokens_output",
                    "outcome",
                    "failure_mode",
                    "redaction_status",
                    "guardrail_status",
                ]
            }
        if name == "list_aspects":
            return self.db.fetch_all(
                """
                SELECT a.id,a.question,a.type,a.version,COUNT(av.trace_id) AS completed_rows
                FROM aspects a LEFT JOIN aspect_values av ON av.aspect_id=a.id
                GROUP BY a.id ORDER BY a.created_at DESC
                """
            )
        if name == "get_representative_traces":
            limit = max(1, min(int(arguments.get("limit", 8)), 20))
            return self.db.fetch_all(
                """
                SELECT id,app,outcome,failure_mode,summary,user_request,what_happened,verbatim_quote
                FROM traces ORDER BY id LIMIT ?
                """,
                (limit,),
            )
        if name == "get_trace_rows":
            trace_ids = list(dict.fromkeys(arguments.get("trace_ids") or []))[:50]
            if not trace_ids:
                return []
            placeholders = ",".join("?" for _ in trace_ids)
            return self.db.fetch_all(
                f"""
                SELECT id,app,outcome,failure_mode,summary,user_request,what_happened,
                       verbatim_quote,redaction_status,guardrail_status
                FROM traces WHERE id IN ({placeholders}) ORDER BY id
                """,
                tuple(trace_ids),
            )
        raise KeyError(f"Unknown read-only tool: {name}")


def mcp_response(tools: ReadOnlyTraceTools, message: dict[str, Any]) -> dict[str, Any]:
    request_id = message.get("id")
    method = message.get("method")
    try:
        if method == "initialize":
            result: Any = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "raft-redacted-traces", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOL_DEFINITIONS}
        elif method == "tools/call":
            params = message.get("params") or {}
            output = tools.call(str(params.get("name")), params.get("arguments") or {})
            result = {
                "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
                "structuredContent": {"result": output},
                "isError": False,
            }
        elif method == "notifications/initialized":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except (KeyError, TypeError, ValueError) as error:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": str(error)},
        }
