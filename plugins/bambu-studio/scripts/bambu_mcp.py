#!/usr/bin/env python3
"""Minimal stdio MCP server for the local Bambu Studio Codex plugin."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

from bambu_common import (
    add_reassembly_plate,
    inspect_project,
    make_request,
    open_in_studio,
    partition_for_printer,
    run_agent_request,
    status,
    validate_3mf_layout,
)


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "name": "bambu_status",
            "description": "Show local Bambu Studio plugin, custom app, and agent API availability.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "bambu_project_inspect",
            "description": "Inspect a Bambu Studio 3MF through the custom agent API.",
            "inputSchema": {
                "type": "object",
                "properties": {"input": {"type": "string", "description": "Absolute path to a .3mf file."}},
                "required": ["input"],
                "additionalProperties": False,
            },
        },
        {
            "name": "bambu_agent_run",
            "description": "Run an arbitrary Bambu Studio agent API method with JSON params.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                    "request_id": {"type": "string"},
                },
                "required": ["method"],
                "additionalProperties": False,
            },
        },
        {
            "name": "bambu_project_partition_for_printer",
            "description": "Cut BoatSmall into printable dovetail pieces, preserving the two existing example pieces and producing one piece per plate.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Absolute path to the source .3mf."},
                    "output": {"type": "string", "description": "Absolute path for the output .3mf."},
                    "added_subpieces": {"type": "integer", "default": 5, "minimum": 1},
                },
                "required": ["input", "output"],
                "additionalProperties": False,
            },
        },
        {
            "name": "bambu_project_add_reassembly_plate",
            "description": "Add a new plate that copies the current project pieces and arranges them back into the source model shape.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Absolute path to the generated .3mf to update."},
                    "output": {"type": "string", "description": "Optional output .3mf path. Defaults to updating input in place."},
                    "source": {"type": "string", "description": "Optional original source .3mf used for reassembly transforms."},
                },
                "required": ["input"],
                "additionalProperties": False,
            },
        },
        {
            "name": "bambu_validate_3mf_layout",
            "description": "Validate 3MF plate/build metadata structurally. This does not replace UI verification.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                    "required_plate_count": {"type": "integer", "minimum": 1},
                    "required_build_item_count": {"type": "integer", "minimum": 1},
                    "require_one_object_per_plate": {"type": "boolean", "default": False},
                    "required_plate_instance_counts": {
                        "type": "object",
                        "description": "Optional map of 1-based plate id to expected model_instance count, e.g. {\"9\": 8}.",
                    },
                    "require_distinct_build_positions": {"type": "boolean", "default": False},
                },
                "required": ["input"],
                "additionalProperties": False,
            },
        },
        {
            "name": "bambu_open_in_studio",
            "description": "Open a local 3MF in the custom Bambu Studio app for visual verification.",
            "inputSchema": {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            },
        },
    ]


def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handlers: dict[str, ToolHandler] = {
        "bambu_status": lambda _args: {"ok": True, "summary": "Bambu Studio plugin status", "status": status()},
        "bambu_project_inspect": lambda args: inspect_project(args["input"]),
        "bambu_agent_run": lambda args: run_agent_request(make_request(args["method"], args.get("params", {}), args.get("request_id"))),
        "bambu_project_partition_for_printer": lambda args: partition_for_printer(
            args["input"], args["output"], int(args.get("added_subpieces", 5))
        ),
        "bambu_project_add_reassembly_plate": lambda args: add_reassembly_plate(
            args["input"], args.get("output"), args.get("source")
        ),
        "bambu_validate_3mf_layout": lambda args: validate_3mf_layout(
            args["input"],
            required_plate_count=args.get("required_plate_count"),
            required_build_item_count=args.get("required_build_item_count"),
            require_one_object_per_plate=bool(args.get("require_one_object_per_plate", False)),
            required_plate_instance_counts={
                int(key): int(value)
                for key, value in (args.get("required_plate_instance_counts") or {}).items()
            },
            require_distinct_build_positions=bool(args.get("require_distinct_build_positions", False)),
        ),
        "bambu_open_in_studio": lambda args: open_in_studio(args["input"]),
    }
    if name not in handlers:
        return {"ok": False, "summary": f"unknown tool: {name}", "errors": [{"code": "unknown_tool", "message": name}]}
    return handlers[name](arguments)


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result_response(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    if message_id is None:
        return None
    if method == "initialize":
        return result_response(
            message_id,
            {
                "protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bambu-studio", "version": "0.2.2"},
            },
        )
    if method == "tools/list":
        return result_response(message_id, {"tools": tool_schema()})
    if method == "tools/call":
        params = message.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments") or {}
        payload = handle_tool(name, arguments)
        return result_response(
            message_id,
            {
                "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
                "isError": not bool(payload.get("ok", True)),
            },
        )
    return error_response(message_id, -32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = dispatch(message)
            if response is not None:
                send(response)
        except Exception as exc:  # noqa: BLE001 - protocol boundary.
            send(error_response(None, -32603, f"{type(exc).__name__}: {exc}", traceback.format_exc()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
