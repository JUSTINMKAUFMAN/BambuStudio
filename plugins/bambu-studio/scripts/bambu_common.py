#!/usr/bin/env python3
"""Shared helpers for the local Bambu Studio Codex plugin."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def q(name: str) -> str:
    return f"{{{CORE_NS}}}{name}"


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def candidate_repos() -> list[Path]:
    candidates: list[Path] = []
    env_repo = os.environ.get("BAMBU_STUDIO_REPO")
    if env_repo:
        candidates.append(Path(env_repo).expanduser())
    candidates.append(plugin_root().parents[1])
    candidates.append(Path("/Users/justin/Documents/BambuStudio"))
    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])
    return candidates


def resolve_repo() -> Path:
    for candidate in candidate_repos():
        if (candidate / "resources/agent/bambu_agent.py").exists():
            return candidate.resolve()
    return candidate_repos()[0].resolve()


def resolve_agent_script(repo: Path | None = None) -> Path:
    root = repo or resolve_repo()
    return root / "resources/agent/bambu_agent.py"


def resolve_app(repo: Path | None = None) -> Path | None:
    env_app = os.environ.get("BAMBU_STUDIO_APP")
    candidates: list[Path] = []
    if env_app:
        candidates.append(Path(env_app).expanduser())
    root = repo or resolve_repo()
    candidates.extend(
        [
            root / "build/arm64/BambuStudio/BambuStudio.app/Contents/MacOS/BambuStudio",
            root / "build/BambuStudio/BambuStudio.app/Contents/MacOS/BambuStudio",
        ]
    )
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def status() -> dict[str, Any]:
    repo = resolve_repo()
    app = resolve_app(repo)
    agent = resolve_agent_script(repo)
    return {
        "plugin_root": str(plugin_root()),
        "repo": str(repo),
        "app_executable": str(app) if app else None,
        "app_available": app is not None,
        "agent_script": str(agent),
        "agent_available": agent.exists(),
        "agent_api_docs": str(repo / "docs/agent-api/README.md"),
        "agent_api_docs_available": (repo / "docs/agent-api/README.md").exists(),
    }


def make_request(method: str, params: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "request_id": request_id or f"codex-{uuid.uuid4()}",
        "method": method,
        "params": params,
    }


def run_agent_request(request: dict[str, Any], prefer_app: bool = True) -> dict[str, Any]:
    repo = resolve_repo()
    agent = resolve_agent_script(repo)
    app = resolve_app(repo)
    if not agent.exists():
        raise FileNotFoundError(f"Bambu agent script not found: {agent}")

    with tempfile.TemporaryDirectory(prefix="bambu-plugin-agent-") as td:
        tmp = Path(td)
        request_path = tmp / "request.json"
        response_path = tmp / "response.json"
        request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n")

        attempts: list[dict[str, Any]] = []
        commands: list[list[str]] = []
        if prefer_app and app is not None:
            commands.append([str(app), "--agent-run", str(request_path), "--agent-out", str(response_path)])
        commands.append(["python3", str(agent), "--request", str(request_path), "--response", str(response_path)])

        for command in commands:
            completed = subprocess.run(command, cwd=repo, text=True, capture_output=True, check=False)
            attempts.append(
                {
                    "command": command,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if response_path.exists():
                try:
                    result = json.loads(response_path.read_text())
                except json.JSONDecodeError:
                    result = {
                        "ok": False,
                        "summary": "agent response was not valid JSON",
                        "errors": [{"code": "invalid_json", "message": response_path.read_text()[:4000]}],
                    }
                result.setdefault("transport_attempts", attempts)
                return result

        return {
            "ok": False,
            "summary": "agent request did not produce a response file",
            "errors": [{"code": "no_response", "message": "No command wrote the expected response JSON."}],
            "transport_attempts": attempts,
        }


def inspect_project(input_path: str) -> dict[str, Any]:
    return run_agent_request(make_request("project.inspect", {"input": str(Path(input_path).expanduser())}))


def partition_for_printer(input_path: str, output_path: str, added_subpieces: int = 5) -> dict[str, Any]:
    return run_agent_request(
        make_request(
            "project.auto_partition_for_printer",
            {
                "input": str(Path(input_path).expanduser()),
                "output": str(Path(output_path).expanduser()),
                "added_subpieces": added_subpieces,
            },
        )
    )


def add_reassembly_plate(input_path: str, output_path: str | None = None, source_path: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"input": str(Path(input_path).expanduser())}
    if output_path:
        params["output"] = str(Path(output_path).expanduser())
    if source_path:
        params["source"] = str(Path(source_path).expanduser())
    return run_agent_request(make_request("project.add_reassembly_plate", params))


def _metadata_value(node: ET.Element, key: str) -> str | None:
    child = node.find(f"metadata[@key='{key}']")
    return child.attrib.get("value") if child is not None else None


def _parse_transform(value: str | None) -> list[float]:
    if not value:
        return [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    values = [float(part) for part in value.split()]
    return values if len(values) == 12 else []


def validate_3mf_layout(
    input_path: str,
    required_plate_count: int | None = None,
    required_build_item_count: int | None = None,
    require_one_object_per_plate: bool = False,
    required_plate_instance_counts: dict[int, int] | None = None,
    require_distinct_build_positions: bool = False,
) -> dict[str, Any]:
    path = Path(input_path).expanduser()
    issues: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {
        "input": str(path),
        "ui_verification_required": True,
        "ui_verification_reason": "Bambu Studio can ignore or reinterpret structurally valid 3MF metadata.",
    }

    if not path.exists():
        return {"ok": False, "summary": f"file does not exist: {path}", "issues": [f"missing file: {path}"], "details": details}

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        details["archive_entries"] = len(names)
        if "3D/3dmodel.model" not in names:
            issues.append("missing 3D/3dmodel.model")
        if "Metadata/model_settings.config" not in names:
            issues.append("missing Metadata/model_settings.config")
        if issues:
            return {"ok": False, "summary": "3MF is missing required Bambu Studio entries", "issues": issues, "warnings": warnings, "details": details}

        model_root = ET.fromstring(zf.read("3D/3dmodel.model"))
        settings_root = ET.fromstring(zf.read("Metadata/model_settings.config"))

    build_items = []
    for item in model_root.findall(f".//{q('build')}/{q('item')}"):
        transform = _parse_transform(item.attrib.get("transform"))
        build_items.append(
            {
                "object_id": item.attrib.get("objectid"),
                "transform": transform,
                "translation": transform[9:12] if len(transform) == 12 else None,
                "printable": item.attrib.get("printable", "1"),
            }
        )
    details["build_item_count"] = len(build_items)
    details["build_object_ids"] = [item["object_id"] for item in build_items]

    resource_objects = model_root.findall(f".//{q('resources')}/{q('object')}")
    details["resource_object_count"] = len(resource_objects)
    details["resource_object_ids"] = [obj.attrib.get("id") for obj in resource_objects]

    plates = []
    for plate in settings_root.findall("plate"):
        instances = []
        for instance in plate.findall("model_instance"):
            instances.append(
                {
                    "object_id": _metadata_value(instance, "object_id"),
                    "instance_id": _metadata_value(instance, "instance_id"),
                    "identify_id": _metadata_value(instance, "identify_id"),
                }
            )
        asset_refs = {
            key: _metadata_value(plate, key)
            for key in ("thumbnail_file", "thumbnail_no_light_file", "top_file", "pick_file")
        }
        missing_asset_refs = [key for key, asset in asset_refs.items() if not asset]
        missing_assets = [asset for asset in asset_refs.values() if asset and asset not in names]
        plates.append(
            {
                "plater_id": _metadata_value(plate, "plater_id"),
                "plater_name": _metadata_value(plate, "plater_name"),
                "instances": instances,
                "instance_count": len(instances),
                "asset_refs": asset_refs,
                "missing_asset_refs": missing_asset_refs,
                "missing_assets": missing_assets,
            }
        )
    details["plate_count"] = len(plates)
    details["plates"] = plates

    if required_plate_count is not None and len(plates) != required_plate_count:
        issues.append(f"expected {required_plate_count} plates, found {len(plates)}")
    if required_build_item_count is not None and len(build_items) != required_build_item_count:
        issues.append(f"expected {required_build_item_count} build items, found {len(build_items)}")
    if require_one_object_per_plate:
        bad = [plate["plater_id"] for plate in plates if plate["instance_count"] != 1]
        if bad:
            issues.append(f"plates without exactly one model_instance: {bad}")
    if required_plate_instance_counts:
        by_plate = {}
        for index, plate in enumerate(plates, 1):
            plater_id = plate["plater_id"]
            by_plate[index] = plate["instance_count"]
            if plater_id is not None:
                try:
                    by_plate[int(plater_id)] = plate["instance_count"]
                except ValueError:
                    pass
        bad_counts = {
            plate_id: expected
            for plate_id, expected in required_plate_instance_counts.items()
            if by_plate.get(int(plate_id)) != expected
        }
        if bad_counts:
            issues.append(
                "plate instance counts differ from expected: "
                + ", ".join(f"{plate_id} expected {expected} found {by_plate.get(int(plate_id))}" for plate_id, expected in bad_counts.items())
            )
    missing_asset_plates = [plate["plater_id"] for plate in plates if plate["missing_assets"]]
    if missing_asset_plates:
        issues.append(f"plates reference missing thumbnail/top/pick assets: {missing_asset_plates}")
    missing_asset_ref_plates = [plate["plater_id"] for plate in plates if plate["missing_asset_refs"]]
    if missing_asset_ref_plates:
        issues.append(f"plates are missing thumbnail/top/pick metadata references: {missing_asset_ref_plates}")

    build_ids = {str(item["object_id"]) for item in build_items}
    instance_ids = {str(instance["object_id"]) for plate in plates for instance in plate["instances"] if instance["object_id"] is not None}
    if instance_ids and build_ids and instance_ids != build_ids:
        issues.append(f"plate instance object ids do not match build item ids: plate={sorted(instance_ids)} build={sorted(build_ids)}")

    translations = [tuple(item["translation"] or []) for item in build_items]
    if require_distinct_build_positions and len(set(translations)) != len(translations):
        issues.append("build item translations are not distinct")
    elif len(set(translations)) != len(translations):
        warnings.append("some build item translations are identical")

    assemble_items = settings_root.findall("assemble/assemble_item")
    details["assemble_item_count"] = len(assemble_items)
    if assemble_items and len(assemble_items) != len(build_items):
        warnings.append(f"assemble item count {len(assemble_items)} differs from build item count {len(build_items)}")

    ok = not issues
    summary = "3MF structure passed validation" if ok else "3MF structure failed validation"
    return {"ok": ok, "summary": summary, "issues": issues, "warnings": warnings, "details": details}


def open_in_studio(input_path: str) -> dict[str, Any]:
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        return {"ok": False, "summary": f"file does not exist: {path}"}
    app = resolve_app()
    if app is not None:
        bundle = app.parents[2]
        subprocess.Popen(["open", "-a", str(bundle), str(path)])
        return {"ok": True, "summary": f"opened {path} in {bundle}", "app": str(bundle)}
    subprocess.Popen(["open", str(path)])
    return {"ok": True, "summary": f"opened {path} with the system default app", "app": None}
