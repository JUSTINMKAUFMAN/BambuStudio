#!/usr/bin/env python3
"""CLI fallback for the Bambu Studio Codex plugin."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok", True) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Codex helper for Bambu Studio agent workflows.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show plugin, app, and agent paths.")

    inspect_parser = sub.add_parser("inspect", help="Inspect a Bambu Studio 3MF file.")
    inspect_parser.add_argument("input")

    partition_parser = sub.add_parser("partition", help="Run the current dovetail partition agent method.")
    partition_parser.add_argument("input")
    partition_parser.add_argument("output")
    partition_parser.add_argument("--added-subpieces", type=int, default=5)

    reassemble_parser = sub.add_parser("reassemble", help="Add a plate that copies pieces and arranges them back into source-model shape.")
    reassemble_parser.add_argument("input")
    reassemble_parser.add_argument("--output")
    reassemble_parser.add_argument("--source")

    validate_parser = sub.add_parser("validate", help="Validate 3MF plate/build structure without claiming UI rendering.")
    validate_parser.add_argument("input")
    validate_parser.add_argument("--required-plate-count", type=int)
    validate_parser.add_argument("--required-build-item-count", type=int)
    validate_parser.add_argument("--one-object-per-plate", action="store_true")
    validate_parser.add_argument(
        "--plate-instance-count",
        action="append",
        default=[],
        metavar="PLATE=COUNT",
        help="Require a specific plate to have COUNT model instances. Can be repeated.",
    )
    validate_parser.add_argument("--distinct-build-positions", action="store_true")

    run_parser = sub.add_parser("run", help="Run an arbitrary agent API method.")
    run_parser.add_argument("method")
    run_parser.add_argument("--params-json", default="{}")
    run_parser.add_argument("--params-file")
    run_parser.add_argument("--request-id")

    open_parser = sub.add_parser("open", help="Open a 3MF in the local custom Bambu Studio build.")
    open_parser.add_argument("input")

    args = parser.parse_args()
    if args.command == "status":
        return emit({"ok": True, "summary": "Bambu Studio plugin status", "status": status()})
    if args.command == "inspect":
        return emit(inspect_project(args.input))
    if args.command == "partition":
        return emit(partition_for_printer(args.input, args.output, args.added_subpieces))
    if args.command == "reassemble":
        return emit(add_reassembly_plate(args.input, args.output, args.source))
    if args.command == "validate":
        required_plate_counts = {}
        for raw in args.plate_instance_count:
            plate, count = raw.split("=", 1)
            required_plate_counts[int(plate)] = int(count)
        return emit(
            validate_3mf_layout(
                args.input,
                required_plate_count=args.required_plate_count,
                required_build_item_count=args.required_build_item_count,
                require_one_object_per_plate=args.one_object_per_plate,
                required_plate_instance_counts=required_plate_counts,
                require_distinct_build_positions=args.distinct_build_positions,
            )
        )
    if args.command == "run":
        if args.params_file:
            params = json.loads(Path(args.params_file).expanduser().read_text())
        else:
            params = json.loads(args.params_json)
        return emit(run_agent_request(make_request(args.method, params, args.request_id)))
    if args.command == "open":
        return emit(open_in_studio(args.input))
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
