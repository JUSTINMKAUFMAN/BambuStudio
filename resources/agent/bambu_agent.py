#!/usr/bin/env python3
"""
Small local automation entrypoint for Codex-facing Bambu Studio builds.

The C++ app invokes this script for --agent-run requests.  It deliberately uses
only the Python standard library plus numpy, which is already present on this
machine, so the custom app can run it without a project virtualenv.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

ET.register_namespace("", CORE_NS)
ET.register_namespace("BambuStudio", BAMBU_NS)
ET.register_namespace("p", PROD_NS)


def q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def fmt(value: float) -> str:
    if abs(value) < 1e-9:
        value = 0.0
    return f"{value:.9g}"


def matrix_string(scale: float, x: float, y: float, z: float) -> str:
    return transform_string(np.eye(3), scale, x, y, z)


def transform_string(rotation: np.ndarray, scale: float, x: float, y: float, z: float) -> str:
    matrix = np.asarray(rotation, dtype=float) * scale
    values = [
        matrix[0, 0],
        matrix[1, 0],
        matrix[2, 0],
        matrix[0, 1],
        matrix[1, 1],
        matrix[2, 1],
        matrix[0, 2],
        matrix[1, 2],
        matrix[2, 2],
        x,
        y,
        z,
    ]
    return " ".join(map(fmt, values))


def parse_transform(value: str) -> list[float]:
    vals = [float(v) for v in value.split()]
    if len(vals) != 12:
        raise ValueError(f"expected 12 transform values, got {len(vals)}")
    return vals


def response(request: dict[str, Any], ok: bool, summary: str, **extra: Any) -> dict[str, Any]:
    out = {
        "ok": ok,
        "request_id": request.get("request_id", ""),
        "schema_version": request.get("schema_version", 1),
        "summary": summary,
        "errors": [] if ok else [{"code": extra.pop("error_code", "agent_error"), "message": summary}],
        "warnings": extra.pop("warnings", []),
        "artifacts": extra.pop("artifacts", []),
    }
    out.update(extra)
    return out


@dataclass
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    @property
    def size(self) -> np.ndarray:
        lo, hi = self.bounds
        return hi - lo


@dataclass
class Volume:
    resource_id: int
    name: str
    mesh: Mesh
    subtype: str = "normal_part"


@dataclass
class Piece:
    wrapper_id: int
    file_index: int
    name: str
    volumes: list[Volume] = field(default_factory=list)
    source_scale: float = 1.0
    source_object_id: int = 0

    @property
    def main_mesh(self) -> Mesh:
        return self.volumes[0].mesh


def read_xml(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def load_object_mesh(path: Path) -> tuple[int, Mesh]:
    root = read_xml(path)
    obj = root.find(f".//{q(CORE_NS, 'object')}")
    if obj is None:
        raise ValueError(f"no object in {path}")
    vertices = [
        [float(v.attrib["x"]), float(v.attrib["y"]), float(v.attrib["z"])]
        for v in obj.findall(f".//{q(CORE_NS, 'vertex')}")
    ]
    faces = [
        [int(t.attrib["v1"]), int(t.attrib["v2"]), int(t.attrib["v3"])]
        for t in obj.findall(f".//{q(CORE_NS, 'triangle')}")
    ]
    return int(obj.attrib["id"]), Mesh(np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64))


def object_names(settings_path: Path) -> dict[int, str]:
    if not settings_path.exists():
        return {}
    root = read_xml(settings_path)
    names: dict[int, str] = {}
    for obj in root.findall("object"):
        oid = int(obj.attrib["id"])
        name = obj.find("metadata[@key='name']")
        if name is not None:
            names[oid] = name.attrib.get("value", f"Object {oid}")
    return names


def top_level_objects(root: ET.Element) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for obj in root.findall(f".//{q(CORE_NS, 'resources')}/{q(CORE_NS, 'object')}"):
        comps = obj.find(q(CORE_NS, "components"))
        if comps is None:
            continue
        comp = comps.find(q(CORE_NS, "component"))
        if comp is None:
            continue
        out.append(
            {
                "wrapper_id": int(obj.attrib["id"]),
                "component_id": int(comp.attrib["objectid"]),
                "path": comp.attrib[q(PROD_NS, "path")].lstrip("/"),
                "uuid": obj.attrib.get(q(PROD_NS, "UUID"), str(uuid.uuid4())),
            }
        )
    return out


def build_items(root: ET.Element) -> dict[int, list[float]]:
    items: dict[int, list[float]] = {}
    for item in root.findall(f".//{q(CORE_NS, 'build')}/{q(CORE_NS, 'item')}"):
        items[int(item.attrib["objectid"])] = parse_transform(item.attrib.get("transform", "1 0 0 0 1 0 0 0 1 0 0 0"))
    return items


def parse_point(value: str) -> tuple[float, float]:
    x_str, y_str = value.split("x", 1)
    return float(x_str), float(y_str)


def project_bed_size(work: Path) -> tuple[float, float]:
    settings_path = work / "Metadata/project_settings.config"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            points = [parse_point(str(p)) for p in settings.get("printable_area", [])]
            if points:
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                width = max(xs) - min(xs)
                depth = max(ys) - min(ys)
                if width > 0 and depth > 0:
                    return width, depth
        except Exception:
            pass
    return 256.0, 256.0


def plate_origin(index: int, count: int, bed_width: float, bed_depth: float) -> tuple[float, float]:
    cols = int(round(math.sqrt(count)))
    if math.sqrt(count) > cols:
        cols += 1
    gap = 1.0 / 5.0
    row = index // cols
    col = index % cols
    return col * bed_width * (1.0 + gap), -row * bed_depth * (1.0 + gap)


def all_piece_vertices(piece: Piece) -> np.ndarray:
    return np.vstack([volume.mesh.vertices for volume in piece.volumes if len(volume.mesh.vertices) > 0])


def auto_orientation(piece: Piece) -> np.ndarray:
    lo, hi = piece.main_mesh.bounds
    size = hi - lo
    axis = int(np.argmin(size))
    sorted_size = np.sort(size)
    if len(sorted_size) >= 2 and sorted_size[0] > sorted_size[1] * 0.85:
        return np.eye(3)
    if axis == 0:
        return np.asarray([[0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])
    if axis == 1:
        return np.asarray([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
    return np.eye(3)


def placement_transform(piece: Piece, plate_index: int, plate_count: int, bed_size: tuple[float, float]) -> str:
    bed_width, bed_depth = bed_size
    origin_x, origin_y = plate_origin(plate_index, plate_count, bed_width, bed_depth)
    target = np.asarray([origin_x + bed_width * 0.5, origin_y + bed_depth * 0.5, 0.0])
    rotation = auto_orientation(piece)
    scale = piece.source_scale
    transformed = (rotation @ all_piece_vertices(piece).T).T * scale
    lo = transformed.min(axis=0)
    hi = transformed.max(axis=0)
    tx = target[0] - (lo[0] + hi[0]) * 0.5
    ty = target[1] - (lo[1] + hi[1]) * 0.5
    tz = -lo[2]
    return transform_string(rotation, scale, tx, ty, tz)


def ensure_plate_assets(work: Path, plate_count: int) -> None:
    metadata = work / "Metadata"
    copies = [
        ("plate_1.png", "plate_{idx}.png"),
        ("plate_1_small.png", "plate_{idx}_small.png"),
        ("plate_no_light_1.png", "plate_no_light_{idx}.png"),
        ("top_1.png", "top_{idx}.png"),
        ("pick_1.png", "pick_{idx}.png"),
    ]
    for source_name, dest_pattern in copies:
        source = metadata / source_name
        if not source.exists():
            continue
        for idx in range(2, plate_count + 1):
            shutil.copyfile(source, metadata / dest_pattern.format(idx=idx))


def inspect_project(input_3mf: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bambu-agent-inspect-") as td:
        work = Path(td)
        with zipfile.ZipFile(input_3mf) as zf:
            zf.extractall(work)
        root = read_xml(work / "3D/3dmodel.model")
        names = object_names(work / "Metadata/model_settings.config")
        transforms = build_items(root)
        objects = []
        for obj in top_level_objects(root):
            _, mesh = load_object_mesh(work / obj["path"])
            tr = transforms.get(obj["wrapper_id"], [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
            lo, hi = mesh.bounds
            objects.append(
                {
                    "object_id": obj["wrapper_id"],
                    "component_id": obj["component_id"],
                    "name": names.get(obj["wrapper_id"], f"Object {obj['wrapper_id']}"),
                    "path": obj["path"],
                    "vertices": int(len(mesh.vertices)),
                    "triangles": int(len(mesh.faces)),
                    "local_bounds": {"min": lo.tolist(), "max": hi.tolist()},
                    "local_size": mesh.size.tolist(),
                    "transform": tr,
                }
            )
        return {
            "input": str(input_3mf),
            "objects": objects,
            "object_count": len(objects),
        }


def clip_polygon(poly: list[np.ndarray], axis: int, value: float, keep_less_equal: bool) -> list[np.ndarray]:
    if not poly:
        return []
    out: list[np.ndarray] = []
    eps = 1e-7

    def inside(p: np.ndarray) -> bool:
        return p[axis] <= value + eps if keep_less_equal else p[axis] >= value - eps

    prev = poly[-1]
    prev_inside = inside(prev)
    for curr in poly:
        curr_inside = inside(curr)
        if curr_inside != prev_inside:
            denom = curr[axis] - prev[axis]
            if abs(denom) > eps:
                t = (value - prev[axis]) / denom
                out.append(prev + t * (curr - prev))
        if curr_inside:
            out.append(curr)
        prev = curr
        prev_inside = curr_inside
    return out


def clip_mesh_slab(mesh: Mesh, low: float, high: float) -> Mesh:
    vertices: list[np.ndarray] = []
    faces: list[list[int]] = []
    for face in mesh.faces:
        poly = [mesh.vertices[int(i)].copy() for i in face]
        poly = clip_polygon(poly, 0, low, keep_less_equal=False)
        poly = clip_polygon(poly, 0, high, keep_less_equal=True)
        if len(poly) < 3:
            continue
        base = len(vertices)
        vertices.extend(poly)
        for i in range(1, len(poly) - 1):
            faces.append([base, base + i, base + i + 1])
    return Mesh(np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64))


def plane_segments(mesh: Mesh, x_value: float) -> list[tuple[np.ndarray, np.ndarray]]:
    eps = 1e-7
    segments: list[tuple[np.ndarray, np.ndarray]] = []
    for face in mesh.faces:
        tri = [mesh.vertices[int(i)] for i in face]
        pts: list[np.ndarray] = []
        for i in range(3):
            a = tri[i]
            b = tri[(i + 1) % 3]
            da = a[0] - x_value
            db = b[0] - x_value
            if abs(da) <= eps and abs(db) <= eps:
                pts.append(a.copy())
                pts.append(b.copy())
            elif da * db < -eps * eps:
                t = (x_value - a[0]) / (b[0] - a[0])
                pts.append(a + t * (b - a))
            elif abs(da) <= eps:
                pts.append(a.copy())
            elif abs(db) <= eps:
                pts.append(b.copy())
        uniq: list[np.ndarray] = []
        for p in pts:
            if not any(np.linalg.norm(p - q) < 1e-5 for q in uniq):
                uniq.append(p)
        if len(uniq) == 2:
            segments.append((uniq[0], uniq[1]))
    return segments


def key2(point: np.ndarray, tol: float = 1e-5) -> tuple[int, int]:
    return (round(float(point[1]) / tol), round(float(point[2]) / tol))


def build_loops(segments: list[tuple[np.ndarray, np.ndarray]]) -> list[list[np.ndarray]]:
    points: dict[tuple[int, int], np.ndarray] = {}
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for a, b in segments:
        ka, kb = key2(a), key2(b)
        if ka == kb:
            continue
        points.setdefault(ka, a)
        points.setdefault(kb, b)
        adjacency.setdefault(ka, []).append(kb)
        adjacency.setdefault(kb, []).append(ka)

    used: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    loops: list[list[np.ndarray]] = []
    for start, neighs in adjacency.items():
        for nxt in neighs:
            edge = (start, nxt)
            if edge in used:
                continue
            loop_keys = [start]
            prev, curr = start, nxt
            while True:
                used.add((prev, curr))
                used.add((curr, prev))
                loop_keys.append(curr)
                choices = [k for k in adjacency.get(curr, []) if k != prev]
                if not choices:
                    break
                if choices[0] == start:
                    break
                prev, curr = curr, choices[0]
                if len(loop_keys) > len(points) + 4:
                    break
            if len(loop_keys) >= 3:
                if loop_keys[-1] == loop_keys[0]:
                    loop_keys = loop_keys[:-1]
                loops.append([points[k] for k in loop_keys])
    unique: list[list[np.ndarray]] = []
    seen: set[frozenset[tuple[int, int]]] = set()
    for loop in loops:
        sig = frozenset(key2(p) for p in loop)
        if sig not in seen and len(sig) >= 3:
            seen.add(sig)
            unique.append(loop)
    return unique


def polygon_area_yz(poly: list[np.ndarray]) -> float:
    area = 0.0
    for i, p in enumerate(poly):
        qv = poly[(i + 1) % len(poly)]
        area += p[1] * qv[2] - qv[1] * p[2]
    return area * 0.5


def point_in_tri_2d(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> bool:
    px, py = p[1], p[2]
    ax, ay = a[1], a[2]
    bx, by = b[1], b[2]
    cx, cy = c[1], c[2]
    v0x, v0y = cx - ax, cy - ay
    v1x, v1y = bx - ax, by - ay
    v2x, v2y = px - ax, py - ay
    den = v0x * v1y - v1x * v0y
    if abs(den) < 1e-12:
        return False
    u = (v2x * v1y - v1x * v2y) / den
    v = (v0x * v2y - v2x * v0y) / den
    return u >= -1e-8 and v >= -1e-8 and u + v <= 1 + 1e-8


def triangulate_loop(loop: list[np.ndarray], want_positive_x_normal: bool) -> tuple[list[np.ndarray], list[list[int]]]:
    if len(loop) < 3:
        return [], []
    poly = list(loop)
    if (polygon_area_yz(poly) > 0) != want_positive_x_normal:
        poly.reverse()
    indices = list(range(len(poly)))
    faces: list[list[int]] = []
    guard = 0
    while len(indices) > 3 and guard < 10000:
        guard += 1
        clipped = False
        for pos, idx in enumerate(indices):
            prev_idx = indices[pos - 1]
            next_idx = indices[(pos + 1) % len(indices)]
            a, b, c = poly[prev_idx], poly[idx], poly[next_idx]
            cross = (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1])
            if cross <= 1e-10:
                continue
            if any(point_in_tri_2d(poly[j], a, b, c) for j in indices if j not in (prev_idx, idx, next_idx)):
                continue
            faces.append([prev_idx, idx, next_idx])
            del indices[pos]
            clipped = True
            break
        if not clipped:
            faces.clear()
            for i in range(1, len(poly) - 1):
                faces.append([0, i, i + 1])
            break
    if len(indices) == 3:
        faces.append(indices)
    return poly, faces


def add_cap(mesh: Mesh, source: Mesh, x_value: float, normal_positive_x: bool) -> Mesh:
    segments = plane_segments(source, x_value)
    loops = build_loops(segments)
    vertices = mesh.vertices.tolist()
    faces = mesh.faces.tolist()
    for loop in loops:
        cap_vertices, cap_faces = triangulate_loop(loop, want_positive_x_normal=normal_positive_x)
        if not cap_vertices:
            continue
        base = len(vertices)
        vertices.extend([p.tolist() for p in cap_vertices])
        faces.extend([[base + a, base + b, base + c] for a, b, c in cap_faces])
    return dedupe_mesh(Mesh(np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64)))


def dedupe_mesh(mesh: Mesh, tol: float = 1e-6) -> Mesh:
    mapping: dict[tuple[int, int, int], int] = {}
    vertices: list[np.ndarray] = []
    faces: list[list[int]] = []
    for face in mesh.faces:
        new_face: list[int] = []
        for idx in face:
            v = mesh.vertices[int(idx)]
            key = tuple(int(round(float(c) / tol)) for c in v)
            if key not in mapping:
                mapping[key] = len(vertices)
                vertices.append(v)
            new_face.append(mapping[key])
        if len(set(new_face)) == 3:
            faces.append(new_face)
    return Mesh(np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64))


def slab_piece(mesh: Mesh, low: float, high: float, global_low: float, global_high: float) -> Mesh:
    clipped = clip_mesh_slab(mesh, low, high)
    if low > global_low + 1e-6:
        clipped = add_cap(clipped, mesh, low, normal_positive_x=False)
    if high < global_high - 1e-6:
        clipped = add_cap(clipped, mesh, high, normal_positive_x=True)
    return dedupe_mesh(clipped)


def trapezoid_prism(center_x: float, sign_x: float, center_y: float, center_z: float, length: float, bottom_width: float, top_width: float, height: float) -> Mesh:
    x0 = center_x
    x1 = center_x + sign_x * length
    yz = [
        (-bottom_width / 2, -height / 2),
        (bottom_width / 2, -height / 2),
        (top_width / 2, height / 2),
        (-top_width / 2, height / 2),
    ]
    verts = []
    for x in (x0, x1):
        for y, z in yz:
            verts.append([x, center_y + y, center_z + z])
    faces = [
        [0, 1, 2], [0, 2, 3],
        [4, 6, 5], [4, 7, 6],
        [0, 4, 5], [0, 5, 1],
        [1, 5, 6], [1, 6, 2],
        [2, 6, 7], [2, 7, 3],
        [3, 7, 4], [3, 4, 0],
    ]
    if sign_x < 0:
        faces = [[a, c, b] for a, b, c in faces]
    return Mesh(np.asarray(verts, dtype=float), np.asarray(faces, dtype=np.int64))


def write_object_model(path: Path, volumes: list[Volume]) -> None:
    model = ET.Element(q(CORE_NS, "model"), {
        "unit": "millimeter",
        q("{http://www.w3.org/XML/1998/namespace}"[1:-1], "lang"): "en-US",
        "requiredextensions": "p",
    })
    model.attrib[q(PROD_NS, "dummy")] = model.attrib.pop(q(PROD_NS, "dummy"), "") if False else model.attrib.get(q(PROD_NS, "dummy"), "")
    if q(PROD_NS, "dummy") in model.attrib:
        del model.attrib[q(PROD_NS, "dummy")]
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "BambuStudio:3mfVersion"}).text = "1"
    resources = ET.SubElement(model, q(CORE_NS, "resources"))
    for volume in volumes:
        obj = ET.SubElement(resources, q(CORE_NS, "object"), {
            "id": str(volume.resource_id),
            q(PROD_NS, "UUID"): str(uuid.uuid4()),
            "type": "model",
        })
        mesh_el = ET.SubElement(obj, q(CORE_NS, "mesh"))
        verts_el = ET.SubElement(mesh_el, q(CORE_NS, "vertices"))
        for v in volume.mesh.vertices:
            ET.SubElement(verts_el, q(CORE_NS, "vertex"), {"x": fmt(v[0]), "y": fmt(v[1]), "z": fmt(v[2])})
        tris_el = ET.SubElement(mesh_el, q(CORE_NS, "triangles"))
        for f in volume.mesh.faces:
            ET.SubElement(tris_el, q(CORE_NS, "triangle"), {"v1": str(int(f[0])), "v2": str(int(f[1])), "v3": str(int(f[2]))})
    ET.indent(model)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(model, encoding="unicode") + "\n")


def make_metadata(parent: ET.Element, key: str, value: str) -> ET.Element:
    return ET.SubElement(parent, "metadata", {"key": key, "value": value})


def metadata_value(parent: ET.Element, key: str, default: str = "") -> str:
    meta = parent.find(f"metadata[@key='{key}']")
    if meta is None:
        return default
    return meta.attrib.get("value", default)


def model_instance_object_id(instance: ET.Element) -> int:
    value = metadata_value(instance, "object_id")
    if not value:
        raise ValueError("model_instance is missing object_id metadata")
    return int(value)


def max_identify_id(settings_root: ET.Element) -> int:
    highest = 100
    for meta in settings_root.findall(".//metadata[@key='identify_id']"):
        try:
            highest = max(highest, int(meta.attrib.get("value", "0")))
        except ValueError:
            continue
    return highest


def object_source_ids(settings_root: ET.Element) -> dict[int, int]:
    source_ids: dict[int, int] = {}
    for obj in settings_root.findall("object"):
        obj_id = int(obj.attrib["id"])
        part = obj.find("part")
        if part is None:
            source_ids[obj_id] = obj_id
            continue
        try:
            source_ids[obj_id] = int(metadata_value(part, "source_object_id", str(obj_id)))
        except ValueError:
            source_ids[obj_id] = obj_id
    return source_ids


def resolve_source_project(input_3mf: Path, settings_root: ET.Element, source_3mf: Path | None) -> Path:
    if source_3mf is not None:
        return source_3mf
    source_files = []
    for part in settings_root.findall(".//part"):
        value = metadata_value(part, "source_file")
        if value.endswith(".3mf") and value not in source_files:
            source_files.append(value)
    for source_file in source_files:
        candidate = input_3mf.parent / source_file
        if candidate.exists():
            return candidate
    raise ValueError("source 3MF was not provided and could not be resolved from part metadata")


def append_plate(
    settings_root: ET.Element,
    plate_index: int,
    instance_rows: list[tuple[int, int]],
) -> None:
    plate = ET.Element("plate")
    make_metadata(plate, "plater_id", str(plate_index))
    make_metadata(plate, "plater_name", f"Plate {plate_index} reassembled")
    make_metadata(plate, "locked", "false")
    make_metadata(plate, "filament_map_mode", "Auto For Flush")
    make_metadata(plate, "thumbnail_file", f"Metadata/plate_{plate_index}.png")
    make_metadata(plate, "thumbnail_no_light_file", f"Metadata/plate_no_light_{plate_index}.png")
    make_metadata(plate, "top_file", f"Metadata/top_{plate_index}.png")
    make_metadata(plate, "pick_file", f"Metadata/pick_{plate_index}.png")

    identify_id = max_identify_id(settings_root)
    for object_id, instance_id in instance_rows:
        inst = ET.SubElement(plate, "model_instance")
        make_metadata(inst, "object_id", str(object_id))
        make_metadata(inst, "instance_id", str(instance_id))
        identify_id += 11
        make_metadata(inst, "identify_id", str(identify_id))

    assemble = settings_root.find("assemble")
    if assemble is None:
        settings_root.append(plate)
    else:
        settings_root.insert(list(settings_root).index(assemble), plate)


def append_assemble_items(
    settings_root: ET.Element,
    instance_rows: list[tuple[int, int]],
    transforms: dict[tuple[int, int], str],
) -> None:
    assemble = settings_root.find("assemble")
    if assemble is None:
        assemble = ET.SubElement(settings_root, "assemble")
    for object_id, instance_id in instance_rows:
        ET.SubElement(assemble, "assemble_item", {
            "object_id": str(object_id),
            "instance_id": str(instance_id),
            "transform": transforms[(object_id, instance_id)],
            "offset": "0 0 0",
        })


def write_model_settings(path: Path, pieces: list[Piece]) -> None:
    config = ET.Element("config")
    identify_id = 100
    for piece in pieces:
        obj = ET.SubElement(config, "object", {"id": str(piece.wrapper_id)})
        make_metadata(obj, "name", piece.name)
        make_metadata(obj, "extruder", "1")
        make_metadata(obj, "agent_generated", "true")
        make_metadata(obj, "dovetail_automation", "Codex Bambu agent")
        make_metadata(obj, "face_count", str(sum(len(v.mesh.faces) for v in piece.volumes)))
        for vol in piece.volumes:
            part = ET.SubElement(obj, "part", {"id": str(vol.resource_id), "subtype": vol.subtype})
            make_metadata(part, "name", vol.name)
            make_metadata(part, "matrix", "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1")
            make_metadata(part, "source_file", "BoatSmall.3mf")
            make_metadata(part, "source_object_id", str(piece.source_object_id))
            make_metadata(part, "source_volume_id", "0")
            make_metadata(part, "source_offset_x", "0")
            make_metadata(part, "source_offset_y", "0")
            make_metadata(part, "source_offset_z", "0")
            ET.SubElement(part, "mesh_stat", {
                "face_count": str(len(vol.mesh.faces)),
                "edges_fixed": "0",
                "degenerate_facets": "0",
                "facets_removed": "0",
                "facets_reversed": "0",
                "backwards_edges": "0",
            })

    for plate_index, piece in enumerate(pieces, 1):
        plate = ET.SubElement(config, "plate")
        make_metadata(plate, "plater_id", str(plate_index))
        make_metadata(plate, "plater_name", f"Plate {plate_index}")
        make_metadata(plate, "locked", "false")
        make_metadata(plate, "filament_map_mode", "Auto For Flush")
        make_metadata(plate, "thumbnail_file", f"Metadata/plate_{plate_index}.png")
        make_metadata(plate, "thumbnail_no_light_file", f"Metadata/plate_no_light_{plate_index}.png")
        make_metadata(plate, "top_file", f"Metadata/top_{plate_index}.png")
        make_metadata(plate, "pick_file", f"Metadata/pick_{plate_index}.png")
        inst = ET.SubElement(plate, "model_instance")
        make_metadata(inst, "object_id", str(piece.wrapper_id))
        make_metadata(inst, "instance_id", "0")
        identify_id += 11
        make_metadata(inst, "identify_id", str(identify_id))

    assemble = ET.SubElement(config, "assemble")
    for piece in pieces:
        ET.SubElement(assemble, "assemble_item", {
            "object_id": str(piece.wrapper_id),
            "instance_id": "0",
            "transform": matrix_string(piece.source_scale, 0, 0, 0),
            "offset": "0 0 0",
        })
    ET.indent(config)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(config, encoding="unicode") + "\n")


def write_main_model(path: Path, pieces: list[Piece], bed_size: tuple[float, float]) -> None:
    model = ET.Element(q(CORE_NS, "model"), {
        "unit": "millimeter",
        q("{http://www.w3.org/XML/1998/namespace}"[1:-1], "lang"): "en-US",
        "requiredextensions": "p",
    })
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "Application"}).text = "BambuStudio-02.07.00.55"
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "BambuStudio:3mfVersion"}).text = "1"
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "CreationDate"}).text = "2026-05-21"
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "ModificationDate"}).text = "2026-05-21"
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "Title"}).text = "BoatSmall agent dovetail partition"
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "Thumbnail_Middle"}).text = "/Metadata/plate_1.png"
    ET.SubElement(model, q(CORE_NS, "metadata"), {"name": "Thumbnail_Small"}).text = "/Metadata/plate_1_small.png"
    resources = ET.SubElement(model, q(CORE_NS, "resources"))
    for piece in pieces:
        obj = ET.SubElement(resources, q(CORE_NS, "object"), {
            "id": str(piece.wrapper_id),
            q(PROD_NS, "UUID"): str(uuid.uuid4()),
            "type": "model",
        })
        comps = ET.SubElement(obj, q(CORE_NS, "components"))
        for vol in piece.volumes:
            ET.SubElement(comps, q(CORE_NS, "component"), {
                q(PROD_NS, "path"): f"/3D/Objects/object_{piece.file_index}.model",
                "objectid": str(vol.resource_id),
                q(PROD_NS, "UUID"): str(uuid.uuid4()),
                "transform": "1 0 0 0 1 0 0 0 1 0 0 0",
            })
    build = ET.SubElement(model, q(CORE_NS, "build"), {q(PROD_NS, "UUID"): str(uuid.uuid4())})
    for idx, piece in enumerate(pieces):
        ET.SubElement(build, q(CORE_NS, "item"), {
            "objectid": str(piece.wrapper_id),
            q(PROD_NS, "UUID"): str(uuid.uuid4()),
            "transform": placement_transform(piece, idx, len(pieces), bed_size),
            "printable": "1",
        })
    ET.indent(model)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(model, encoding="unicode") + "\n")


def write_relationships(path: Path, pieces: list[Piece]) -> None:
    rels = ET.Element("Relationships", {"xmlns": REL_NS})
    for idx, piece in enumerate(pieces, 1):
        ET.SubElement(rels, "Relationship", {
            "Target": f"/3D/Objects/object_{piece.file_index}.model",
            "Id": f"rel-{idx}",
            "Type": "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
        })
    ET.indent(rels)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rels, encoding="unicode") + "\n")


def write_cut_information(path: Path, pieces: list[Piece]) -> None:
    objects = ET.Element("objects")
    for idx, _piece in enumerate(pieces, 1):
        obj = ET.SubElement(objects, "object", {"id": str(idx)})
        ET.SubElement(obj, "cut_id", {"id": "22705", "check_sum": str(len(pieces)), "connectors_cnt": "0"})
    ET.indent(objects)
    path.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(objects, encoding="unicode") + "\n")


def zip_dir(src: Path, dst: Path) -> None:
    if dst.exists():
        dst.unlink()
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src).as_posix())


def partition_boat(input_3mf: Path, output_3mf: Path, added_subpieces: int) -> dict[str, Any]:
    if added_subpieces < 1:
        raise ValueError("added_subpieces must be >= 1")

    with tempfile.TemporaryDirectory(prefix="bambu-agent-partition-") as td:
        work = Path(td)
        with zipfile.ZipFile(input_3mf) as zf:
            zf.extractall(work)

        main_root = read_xml(work / "3D/3dmodel.model")
        names = object_names(work / "Metadata/model_settings.config")
        transforms = build_items(main_root)
        source_objects = top_level_objects(main_root)
        bed_size = project_bed_size(work)

        loaded: list[tuple[dict[str, Any], Mesh]] = []
        for obj in source_objects:
            _rid, mesh = load_object_mesh(work / obj["path"])
            loaded.append((obj, mesh))
        split_index = max(range(len(loaded)), key=lambda i: len(loaded[i][1].faces))

        pieces: list[Piece] = []
        next_wrapper = 2
        next_resource = 1
        next_file_index = 1

        for i, (obj, mesh) in enumerate(loaded):
            scale = transforms.get(obj["wrapper_id"], [1])[0]
            source_name = names.get(obj["wrapper_id"], f"Object {obj['wrapper_id']}")
            if i != split_index:
                piece = Piece(next_wrapper, next_file_index, source_name, source_scale=scale, source_object_id=obj["wrapper_id"])
                piece.volumes.append(Volume(next_resource, source_name, dedupe_mesh(mesh), "normal_part"))
                pieces.append(piece)
                next_wrapper += 2
                next_resource += 2
                next_file_index += 1
                continue

            lo, hi = mesh.bounds
            x_low, x_high = float(lo[0]), float(hi[0])
            segment_count = added_subpieces + 1
            cuts = np.linspace(x_low, x_high, segment_count + 1)
            y_centers = [-10.0, 10.0]
            male_length = max((x_high - x_low) / segment_count * 0.18, 3.0)
            for seg in range(segment_count):
                low = float(cuts[seg])
                high = float(cuts[seg + 1])
                seg_mesh = slab_piece(mesh, low, high, x_low, x_high)
                piece_name = f"{source_name}_agent_piece_{seg + 1:02d}"
                piece = Piece(next_wrapper, next_file_index, piece_name, source_scale=scale, source_object_id=obj["wrapper_id"])
                piece.volumes.append(Volume(next_resource, piece_name, seg_mesh, "normal_part"))
                next_resource += 1

                for cut_idx, cut_x in enumerate(cuts[1:-1], 1):
                    is_left_piece = seg == cut_idx - 1
                    is_right_piece = seg == cut_idx
                    if not (is_left_piece or is_right_piece):
                        continue
                    male_on_left = cut_idx % 2 == 1
                    for conn_idx, y_center in enumerate(y_centers, 1):
                        if (is_left_piece and male_on_left) or (is_right_piece and not male_on_left):
                            sign = 1.0 if is_left_piece else -1.0
                            conn = trapezoid_prism(float(cut_x), sign, y_center, 0.0, male_length, 5.0, 9.0, 8.0)
                            piece.volumes.append(Volume(next_resource, f"{piece_name}_dovetail_male_{cut_idx}_{conn_idx}", conn, "normal_part"))
                        else:
                            sign = -1.0 if is_left_piece else 1.0
                            conn = trapezoid_prism(float(cut_x), sign, y_center, 0.0, male_length * 1.25, 6.0, 10.5, 9.5)
                            piece.volumes.append(Volume(next_resource, f"{piece_name}_dovetail_socket_{cut_idx}_{conn_idx}", conn, "negative_part"))
                        next_resource += 1

                pieces.append(piece)
                next_wrapper += 2
                next_file_index += 1

        objects_dir = work / "3D/Objects"
        if objects_dir.exists():
            shutil.rmtree(objects_dir)
        objects_dir.mkdir(parents=True)
        for piece in pieces:
            write_object_model(objects_dir / f"object_{piece.file_index}.model", piece.volumes)

        ensure_plate_assets(work, len(pieces))
        write_main_model(work / "3D/3dmodel.model", pieces, bed_size)
        write_relationships(work / "3D/_rels/3dmodel.model.rels", pieces)
        write_model_settings(work / "Metadata/model_settings.config", pieces)
        write_cut_information(work / "Metadata/cut_information.xml", pieces)

        output_3mf.parent.mkdir(parents=True, exist_ok=True)
        zip_dir(work, output_3mf)

    return {
        "output": str(output_3mf),
        "piece_count": len(pieces),
        "plates": len(pieces),
        "split_added_subpieces": added_subpieces,
        "piece_names": [p.name for p in pieces],
    }


def transform_values_string(values: list[float]) -> str:
    if len(values) != 12:
        raise ValueError(f"expected 12 transform values, got {len(values)}")
    return " ".join(fmt(v) for v in values)


def add_reassembly_plate(input_3mf: Path, output_3mf: Path, source_3mf: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bambu-agent-reassembly-") as td:
        work = Path(td)
        with zipfile.ZipFile(input_3mf) as zf:
            zf.extractall(work)

        main_path = work / "3D/3dmodel.model"
        settings_path = work / "Metadata/model_settings.config"
        main_root = read_xml(main_path)
        settings_root = read_xml(settings_path)
        bed_width, bed_depth = project_bed_size(work)
        plates = settings_root.findall("plate")
        if not plates:
            raise ValueError("input project has no plate metadata")

        source_path = resolve_source_project(input_3mf, settings_root, source_3mf)
        with tempfile.TemporaryDirectory(prefix="bambu-agent-source-") as source_td:
            source_work = Path(source_td)
            with zipfile.ZipFile(source_path) as zf:
                zf.extractall(source_work)
            source_root = read_xml(source_work / "3D/3dmodel.model")
            source_transforms = build_items(source_root)

        copy_object_ids: list[int] = []
        for plate in plates:
            for instance in plate.findall("model_instance"):
                copy_object_ids.append(model_instance_object_id(instance))
        if not copy_object_ids:
            raise ValueError("input project plates do not reference any model instances")

        source_ids = object_source_ids(settings_root)
        build = main_root.find(f".//{q(CORE_NS, 'build')}")
        if build is None:
            raise ValueError("3D model has no build section")

        new_plate_index = len(plates) + 1
        origin_x, origin_y = plate_origin(new_plate_index - 1, new_plate_index, bed_width, bed_depth)
        existing_counts = Counter(
            int(item.attrib["objectid"])
            for item in build.findall(q(CORE_NS, "item"))
        )

        instance_rows: list[tuple[int, int]] = []
        added_transforms: dict[tuple[int, int], str] = {}
        for object_id in copy_object_ids:
            source_object_id = source_ids.get(object_id, object_id)
            if source_object_id not in source_transforms:
                raise ValueError(f"source object {source_object_id} for generated object {object_id} has no build transform")
            transform = list(source_transforms[source_object_id])
            transform[9] += origin_x
            transform[10] += origin_y
            transform_str = transform_values_string(transform)
            instance_id = existing_counts[object_id]
            existing_counts[object_id] += 1
            instance_rows.append((object_id, instance_id))
            added_transforms[(object_id, instance_id)] = transform_str
            ET.SubElement(build, q(CORE_NS, "item"), {
                "objectid": str(object_id),
                q(PROD_NS, "UUID"): str(uuid.uuid4()),
                "transform": transform_str,
                "printable": "1",
            })

        ensure_plate_assets(work, new_plate_index)
        append_plate(settings_root, new_plate_index, instance_rows)
        append_assemble_items(settings_root, instance_rows, added_transforms)

        ET.indent(main_root)
        main_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(main_root, encoding="unicode") + "\n")
        ET.indent(settings_root)
        settings_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(settings_root, encoding="unicode") + "\n")

        output_3mf.parent.mkdir(parents=True, exist_ok=True)
        zip_dir(work, output_3mf)

    return {
        "output": str(output_3mf),
        "source": str(source_path),
        "plates": new_plate_index,
        "new_plate": new_plate_index,
        "copied_instances": len(instance_rows),
        "instance_rows": [{"object_id": oid, "instance_id": iid} for oid, iid in instance_rows],
    }


def run_request(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("schema_version") not in (None, 1):
        return response(request, False, f"unsupported schema_version {request.get('schema_version')}", error_code="unsupported_schema")
    method = request.get("method")
    params = request.get("params", {})
    try:
        if method == "project.inspect":
            project = inspect_project(Path(params["input"]).expanduser())
            return response(request, True, f"inspected {project['object_count']} objects", project=project)
        if method in ("project.auto_partition_for_printer", "project.boat_subdivide_dovetail"):
            result = partition_boat(
                Path(params["input"]).expanduser(),
                Path(params["output"]).expanduser(),
                int(params.get("added_subpieces", params.get("new_subpieces", 5))),
            )
            return response(request, True, f"wrote {result['piece_count']} pieces on {result['plates']} plates", artifacts=[result["output"]], result=result)
        if method in ("project.add_reassembly_plate", "project.boat_add_reassembly_plate"):
            source_param = params.get("source")
            result = add_reassembly_plate(
                Path(params["input"]).expanduser(),
                Path(params.get("output", params["input"])).expanduser(),
                Path(source_param).expanduser() if source_param else None,
            )
            return response(request, True, f"added reassembly plate {result['new_plate']} with {result['copied_instances']} copied instances", artifacts=[result["output"]], result=result)
        return response(request, False, f"unsupported method {method}", error_code="unsupported_method")
    except Exception as exc:  # noqa: BLE001 - this is a CLI boundary.
        return response(request, False, f"{type(exc).__name__}: {exc}", error_code="exception")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()

    request_path = Path(args.request)
    response_path = Path(args.response)
    request = json.loads(request_path.read_text())
    out = run_request(request)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
