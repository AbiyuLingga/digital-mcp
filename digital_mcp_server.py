#!/usr/bin/env python3
"""MCP server for building, validating and running Digital circuits.

The server deliberately uses only the Python standard library.  An MCP host
provides the natural-language/image understanding; this server turns the
host's structured circuit design into Digital's XML format and uses Digital's
headless CLI for validation and tests.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


ROOT = Path(os.environ.get("DIGITAL_PROJECT_ROOT", Path(__file__).resolve().parents[1])).expanduser().resolve()
SERVER_ROOT = Path(__file__).resolve().parent
GENERATED = Path(os.environ.get("DIGITAL_MCP_OUTPUT_DIR", SERVER_ROOT / "generated")).expanduser().resolve()
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class ToolError(Exception):
    """An expected, user-facing MCP tool error."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _number(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"{name} must be a number")
    return int(value)


def _positive_int(value: Any, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or int(value) < 1:
        raise ToolError(f"{name} must be a positive integer")
    return int(value)


def _safe_output(name: str | None) -> Path:
    filename = name or f"circuit-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.dig"
    if not SAFE_NAME.fullmatch(filename) or not filename.endswith(".dig"):
        raise ToolError("file_name must be a simple .dig filename")
    GENERATED.mkdir(parents=True, exist_ok=True)
    return GENERATED / filename


def _input_path(value: Any, suffix: str | None = None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ToolError("path must be a non-empty string")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ToolError(f"file does not exist: {path}")
    if suffix and path.suffix.lower() != suffix.lower():
        raise ToolError(f"expected a {suffix} file: {path}")
    return path


def _validated_output(value: Any, input_path: Path) -> Path:
    """Resolve an optional output path without allowing arbitrary overwrites.

    Absolute paths are accepted as explicit intent; relative paths must stay in
    the same directory as the input file.
    """
    if not isinstance(value, str) or not value.strip():
        raise ToolError("output_path must be a non-empty string")
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    resolved = (input_path.parent / candidate).resolve()
    if resolved.parent != input_path.parent.resolve():
        raise ToolError(f"output_path must be absolute or stay in the input file's directory: {value}")
    return resolved


def _element_attributes(parent: ET.Element, attrs: dict[str, Any]) -> None:
    entries = ET.SubElement(parent, "elementAttributes")
    for key, value in attrs.items():
        entry = ET.SubElement(entries, "entry")
        ET.SubElement(entry, "string").text = str(key)
        if key == "Testdata":
            data = ET.SubElement(entry, "testData")
            ET.SubElement(data, "dataString").text = str(value)
        elif isinstance(value, bool):
            ET.SubElement(entry, "boolean").text = str(value).lower()
        elif isinstance(value, int) and not isinstance(value, bool):
            ET.SubElement(entry, "int").text = str(value)
        elif isinstance(value, float):
            ET.SubElement(entry, "double").text = str(value)
        else:
            ET.SubElement(entry, "string").text = str(value)


def _pin(element: dict[str, Any], pin: Any, output: bool) -> tuple[int, int]:
    """Resolve the common Digital pin positions used by generated circuits."""
    x = _number(element.get("x", element.get("pos", {}).get("x", 0)), "element.x")
    y = _number(element.get("y", element.get("pos", {}).get("y", 0)), "element.y")
    kind = str(element.get("type", ""))
    if output:
        if kind in {"In", "Const", "Clock", "VDD", "Ground", "PullUp", "PullDown"}:
            return x, y
        if kind in {"Not", "Buffer"}:
            return x + 40, y
        if kind in {"NAnd", "NOr", "XNOr"}:
            return x + 80, y + 20
        return x + 60, y + 20
    index = int(pin or 0)
    if kind in {"Out", "Probe"}:
        return x, y
    if kind in {"Not", "Buffer"}:
        return x, y
    return x, y + index * 40


def _wire_point(design: dict[str, Any], endpoint: Any, output: bool) -> tuple[int, int]:
    if isinstance(endpoint, dict) and "x" in endpoint and "y" in endpoint:
        return _number(endpoint["x"], "wire point.x"), _number(endpoint["y"], "wire point.y")
    if not isinstance(endpoint, str):
        raise ToolError("wire endpoint must be an element id or {x,y}")
    element = next((e for e in design["elements"] if e.get("id") == endpoint), None)
    if element is None:
        raise ToolError(f"wire references unknown element: {endpoint}")
    return _pin(element, 0, output)


def _test_data(tests: list[dict[str, Any]]) -> str:
    if not tests:
        return ""
    input_names = list(tests[0].get("inputs", {}).keys())
    output_names = list(tests[0].get("outputs", {}).keys())
    names = input_names + output_names
    rows = [" ".join(names)]
    for case in tests:
        inputs = case.get("inputs", {})
        outputs = case.get("outputs", {})
        missing = [n for n in names if n not in inputs and n not in outputs]
        if missing:
            raise ToolError(f"test case is missing values for: {', '.join(missing)}")
        rows.append(" ".join(str(inputs.get(n, outputs.get(n))) for n in names))
    return "\n".join(rows)


def build_circuit(design: dict[str, Any], output: Path) -> dict[str, Any]:
    if not isinstance(design, dict):
        raise ToolError("design must be an object")
    elements = design.get("elements", [])
    wires = design.get("wires", [])
    if not isinstance(elements, list) or not isinstance(wires, list):
        raise ToolError("design.elements and design.wires must be arrays")
    ids: set[str] = set()
    root = ET.Element("circuit")
    ET.SubElement(root, "version").text = "1"
    ET.SubElement(root, "attributes")
    visual = ET.SubElement(root, "visualElements")
    for element in elements:
        element_id = element.get("id")
        kind = element.get("type")
        if not isinstance(element_id, str) or not element_id or element_id in ids:
            raise ToolError("each element needs a unique non-empty id")
        if not isinstance(kind, str) or not SAFE_NAME.fullmatch(kind):
            raise ToolError(f"invalid element type: {kind!r}")
        ids.add(element_id)
        ve = ET.SubElement(visual, "visualElement")
        ET.SubElement(ve, "elementName").text = kind
        attrs = dict(element.get("attributes", {}))
        if "label" in element:
            attrs["Label"] = element["label"]
        if "bits" in element:
            attrs["Bits"] = _number(element["bits"], "element.bits")
        if "inputs" in element:
            attrs["Inputs"] = _number(element["inputs"], "element.inputs")
        if kind == "Testcase" and "tests" in design:
            attrs["Testdata"] = _test_data(design["tests"])
        _element_attributes(ve, attrs)
        pos = ET.SubElement(ve, "pos")
        for axis in ("x", "y"):
            value = element.get(axis, element.get("pos", {}).get(axis, 0))
            coordinate = _number(value, f"element.{axis}")
            # ponytail: reject off-grid positions; snapping gates alone detaches wires.
            if value != coordinate or coordinate % 20:
                raise ToolError(f"element {element_id!r} {axis} must be on the 20-unit grid")
            pos.set(axis, str(coordinate))
    wires_node = ET.SubElement(root, "wires")
    for wire in wires:
        if not isinstance(wire, dict):
            raise ToolError("each wire must be an object")
        if "p1" in wire and "p2" in wire:
            p1, p2 = _wire_point(design, wire["p1"], True), _wire_point(design, wire["p2"], False)
        elif "from" in wire and "to" in wire:
            p1 = _wire_point(design, wire["from"], True)
            p2 = _wire_point(design, wire["to"], False)
            if isinstance(wire["to"], str):
                target = next(e for e in elements if e.get("id") == wire["to"])
                p2 = _pin(target, wire.get("input", 0), False)
        else:
            raise ToolError("each wire needs from/to or p1/p2")
        node = ET.SubElement(wires_node, "wire")
        for tag, point in (("p1", p1), ("p2", p2)):
            child = ET.SubElement(node, tag)
            child.set("x", str(point[0]))
            child.set("y", str(point[1]))
    if design.get("tests"):
        # A testcase element is optional for callers that only want a circuit,
        # but adding one makes the generated file directly runnable by Digital.
        if not any(e.get("type") == "Testcase" for e in elements):
            ve = ET.SubElement(visual, "visualElement")
            ET.SubElement(ve, "elementName").text = "Testcase"
            _element_attributes(ve, {"Testdata": _test_data(design["tests"])})
            ET.SubElement(ve, "pos", x="0", y="0")
    ET.SubElement(root, "measurementOrdering")
    ET.indent(root, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode"), encoding="utf-8")
    return inspect_circuit(output)


def inspect_circuit(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ToolError(f"invalid Digital XML: {exc}") from exc
    elements = []
    for ve in root.findall("./visualElements/visualElement"):
        attrs: dict[str, Any] = {}
        entries = ve.find("elementAttributes")
        if entries is not None:
            for entry in entries.findall("entry"):
                key = entry.findtext("string")
                strings = entry.findall("string")
                if len(strings) > 1:
                    value = strings[1].text
                elif entry.find("testData") is not None:
                    value = entry.findtext("testData/dataString")
                else:
                    value = next((child.text for child in list(entry) if child.tag != "string"), None)
                if key:
                    attrs[key] = value
        pos = ve.find("pos")
        elements.append({"type": ve.findtext("elementName"), "x": int(pos.get("x", 0)), "y": int(pos.get("y", 0)), "attributes": attrs})
    wires = []
    for wire in root.findall("./wires/wire"):
        wires.append({tag: {"x": int(wire.find(tag).get("x")), "y": int(wire.find(tag).get("y"))} for tag in ("p1", "p2")})
    return {"path": str(path), "elements": elements, "element_count": len(elements), "wire_count": len(wires), "wires": wires}


def _jar() -> Path:
    candidates = []
    if os.environ.get("DIGITAL_JAR"):
        candidates.append(Path(os.environ["DIGITAL_JAR"]).expanduser())
    candidates += [
        ROOT / "source/target/Digital.jar",
        ROOT / "modern/dist/Digital.jar",
        ROOT / "target/Digital.jar",
        SERVER_ROOT / "Digital.jar",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise ToolError("Digital.jar not found; run Maven build or set DIGITAL_JAR")


def _java() -> str:
    configured = os.environ.get("DIGITAL_JAVA")
    candidates = [
        Path(configured).expanduser() if configured else None,
        ROOT / ".runtime/desktop-runtime/bin/java",
        ROOT / ".runtime/jdk-21.0.12.1+1/Contents/Home/bin/java",
    ]
    for path in candidates:
        if path and path.is_file():
            return str(path)
    return "java"


def run_tests(path: Path, timeout: int, verbose: bool) -> dict[str, Any]:
    command = [_java(), "-Djava.awt.headless=true", "-cp", str(_jar()), "CLI", "test", "-circ", str(path)]
    if verbose:
        command += ["-verbose"]
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Digital test timed out after {timeout}s") from exc
    output = (completed.stdout + completed.stderr).strip()
    return {"passed": completed.returncode == 0, "exit_code": completed.returncode, "output": output, "command": command}


def _export_svg(input_path: Path, output_path: str | None, timeout: int) -> dict[str, Any]:
    """Export a circuit to SVG through Digital's headless CLI."""
    svg = _validated_output(output_path, input_path) if output_path is not None else input_path.with_suffix(input_path.suffix + ".svg")
    svg.parent.mkdir(parents=True, exist_ok=True)
    command = [_java(), "-Djava.awt.headless=true", "-cp", str(_jar()), "CLI", "svg", "-dig", str(input_path), "-svg", str(svg)]
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Digital SVG export timed out after {timeout}s") from exc
    if completed.returncode != 0:
        raise ToolError((completed.stdout + completed.stderr).strip() or "Digital SVG export failed")
    return {"svg_path": str(svg), "command": command, "format": "svg"}


def render_svg(path: Path, timeout: int) -> dict[str, Any]:
    """Backward-compatible SVG export that writes next to the input file."""
    result = _export_svg(path, None, timeout)
    return {"svg_path": result["svg_path"], "command": result["command"]}


SVG_CONVERTERS = ("rsvg-convert", "magick", "convert", "inkscape")


def _resolve_svg_converter() -> str | None:
    """DIGITAL_SVG_CONVERTER wins; otherwise the first converter found on PATH."""
    configured = os.environ.get("DIGITAL_SVG_CONVERTER")
    if configured and shutil.which(configured):
        return configured
    for name in SVG_CONVERTERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def _png_converter_command(
    converter: str, svg_path: Path, output_path: Path, pixel_width: int | None = None
) -> list[str]:
    executable = os.path.basename(str(converter))
    if executable == "rsvg-convert":
        command = [str(converter), "-f", "png"]
        if pixel_width is not None:
            command += ["-w", str(pixel_width)]
        return command + ["-o", str(output_path), str(svg_path)]
    if executable in ("magick", "convert"):
        command = [str(converter), str(svg_path)]
        if pixel_width is not None:
            command += ["-resize", f"{pixel_width}x"]
        return command + [str(output_path)]
    if executable == "inkscape":
        command = [str(converter), str(svg_path), f"--export-filename={output_path}", "--export-format=png"]
        if pixel_width is not None:
            command.append(f"--export-width={pixel_width}")
        return command
    raise ToolError(f"unsupported SVG converter: {converter}")


def _convert_svg_to_png(
    source_svg: Path, destination: Path, converter: str, timeout: int, pixel_width: int | None
) -> list[str]:
    command = _png_converter_command(converter, source_svg, destination, pixel_width)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"SVG-to-PNG conversion timed out after {timeout}s") from exc
    if completed.returncode != 0 or not destination.is_file():
        detail = (completed.stdout + completed.stderr).strip() or "no output written"
        raise ToolError(f"SVG-to-PNG conversion failed ({converter}): {detail}")
    return command


def _export_png(
    input_path: Path, output_path: str | None, timeout: int, pixel_width: int = 2048
) -> dict[str, Any]:
    """Render a circuit to PNG: export the SVG first, then convert it."""
    destination = (
        _validated_output(output_path, input_path)
        if output_path is not None
        else input_path.with_suffix(".png")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    converter = _resolve_svg_converter()
    if converter is None:
        raise ToolError(
            "no SVG-to-PNG converter found; install rsvg-convert (or ImageMagick/Inkscape) or set DIGITAL_SVG_CONVERTER"
        )
    handle, source_name = tempfile.mkstemp(prefix="digital-svg-", suffix=".svg")
    os.close(handle)
    source_svg = Path(source_name)
    try:
        _export_svg(input_path, str(source_svg), timeout)
        command = _convert_svg_to_png(source_svg, destination, converter, timeout, pixel_width)
        return {"image_path": str(destination), "format": "png", "command": command}
    finally:
        source_svg.unlink(missing_ok=True)


def _input_state_spec(inputs: Any) -> str:
    if inputs is None:
        return ""
    if isinstance(inputs, str):
        return inputs.strip()
    if not isinstance(inputs, dict):
        raise ToolError("inputs must be an object such as {\"A\": 1, \"B\": 0} or a NAME=VALUE string")
    parts = []
    for name, value in inputs.items():
        if not isinstance(name, str) or not name.strip():
            raise ToolError("input names must be non-empty strings")
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, (int, float, str)):
            raise ToolError(f"input value for {name} must be a number, boolean or string")
        parts.append(f"{name}={value}")
    return ",".join(parts)


def _state_slug(state_spec: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", state_spec).strip("._-")
    return slug[:80] or "default"


def _simulation_output(input_path: Path, output_path: str | None, fmt: str, state_spec: str) -> Path:
    if output_path is not None:
        return _validated_output(output_path, input_path)
    return input_path.parent / f"{input_path.stem}.simulation-{_state_slug(state_spec)}.{fmt}"


def _export_simulation_svg(
    input_path: Path,
    output_path: Path,
    state_spec: str,
    timeout: int,
    scale: int,
    hide_test: bool,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        _java(),
        "-Djava.awt.headless=true",
        "-cp",
        str(_jar()),
        "CLI",
        "snapshot",
        "-dig",
        str(input_path),
        "-svg",
        str(output_path),
        "-scale",
        str(scale),
    ]
    if state_spec:
        command += ["-inputs", state_spec]
    if not hide_test:
        command.append("-hideTest")
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Digital simulation render timed out after {timeout}s") from exc
    if completed.returncode != 0 or not output_path.is_file():
        detail = (completed.stdout + completed.stderr).strip() or "no output written"
        raise ToolError(f"Digital simulation render failed: {detail}")
    return {"svg_path": str(output_path), "command": command, "format": "svg", "inputs": state_spec}


def _export_simulation_image(
    input_path: Path,
    output_path: str | None,
    fmt: str,
    inputs: Any,
    timeout: int,
    pixel_width: int,
    scale: int,
    hide_test: bool,
) -> dict[str, Any]:
    state_spec = _input_state_spec(inputs)
    destination = _simulation_output(input_path, output_path, fmt, state_spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "svg":
        return _export_simulation_svg(input_path, destination, state_spec, timeout, scale, hide_test)

    converter = _resolve_svg_converter()
    if converter is None:
        raise ToolError(
            "no SVG-to-PNG converter found; install rsvg-convert (or ImageMagick/Inkscape) or set DIGITAL_SVG_CONVERTER"
        )
    handle, source_name = tempfile.mkstemp(prefix="digital-simulation-", suffix=".svg")
    os.close(handle)
    source_svg = Path(source_name)
    try:
        rendered = _export_simulation_svg(input_path, source_svg, state_spec, timeout, scale, hide_test)
        convert_command = _convert_svg_to_png(source_svg, destination, converter, timeout, pixel_width)
        return {
            "image_path": str(destination),
            "format": "png",
            "inputs": state_spec,
            "simulation_command": rendered["command"],
            "conversion_command": convert_command,
        }
    finally:
        source_svg.unlink(missing_ok=True)


def _read_truth_table(path: Path) -> tuple[list[str], list[list[str]]]:
    info = inspect_circuit(path)
    for element in info["elements"]:
        test_data = element["attributes"].get("Testdata")
        if not isinstance(test_data, str) or not test_data.strip():
            continue
        lines = [line.strip() for line in test_data.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        columns = re.split(r"\s+", lines[0])
        rows = [re.split(r"\s+", line) for line in lines[1:]]
        if any(len(row) != len(columns) for row in rows):
            raise ToolError("test data contains a row with a different number of columns")
        return columns, rows
    raise ToolError("no embedded Testcase data found in the .dig file")


def _truth_table_svg(columns: list[str], rows: list[list[str]], validation: dict[str, Any]) -> str:
    cell_widths = [max(100, min(240, 18 * max([len(column)] + [len(row[i]) for row in rows]) + 28)) for i, column in enumerate(columns)]
    left = 24
    title_y = 36
    table_top = 82
    row_height = 36
    width = left * 2 + sum(cell_widths)
    height = table_top + row_height * (len(rows) + 1) + 24
    status = "PASS" if validation.get("passed") else "FAIL"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="{left}" y="{title_y}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="24" font-weight="700" fill="#0f172a">Truth table · {status}</text>',
        f'<text x="{left}" y="62" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="14" fill="#475569">Digital validation: {html.escape(validation.get("output", "").replace(chr(10), " "))}</text>',
        f'<rect x="{left}" y="{table_top}" width="{sum(cell_widths)}" height="{row_height}" fill="#1e293b"/>',
    ]
    x = left
    for column, cell_width in zip(columns, cell_widths):
        parts.append(f'<text x="{x + cell_width / 2}" y="{table_top + 24}" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="15" font-weight="700" fill="white">{html.escape(column)}</text>')
        x += cell_width
    for row_index, row in enumerate(rows):
        y = table_top + row_height * (row_index + 1)
        fill = "#ffffff" if row_index % 2 == 0 else "#e2e8f0"
        parts.append(f'<rect x="{left}" y="{y}" width="{sum(cell_widths)}" height="{row_height}" fill="{fill}"/>')
        x = left
        for value, cell_width in zip(row, cell_widths):
            parts.append(f'<text x="{x + cell_width / 2}" y="{y + 24}" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="15" fill="#0f172a">{html.escape(value)}</text>')
            x += cell_width
    parts.append(f'<rect x="{left}" y="{table_top}" width="{sum(cell_widths)}" height="{row_height * (len(rows) + 1)}" fill="none" stroke="#94a3b8" stroke-width="1.5"/>')
    x = left
    for cell_width in cell_widths[:-1]:
        x += cell_width
        parts.append(f'<line x1="{x}" y1="{table_top}" x2="{x}" y2="{table_top + row_height * (len(rows) + 1)}" stroke="#94a3b8"/>')
    for row_index in range(1, len(rows) + 1):
        y = table_top + row_height * row_index
        parts.append(f'<line x1="{left}" y1="{y}" x2="{left + sum(cell_widths)}" y2="{y}" stroke="#cbd5e1"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def _export_truth_table_image(
    input_path: Path, output_path: str | None, fmt: str, timeout: int, pixel_width: int
) -> dict[str, Any]:
    columns, rows = _read_truth_table(input_path)
    validation = run_tests(input_path, timeout, True)
    destination = (
        _validated_output(output_path, input_path)
        if output_path is not None
        else input_path.with_suffix(f".truth-table.{fmt}")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    svg_content = _truth_table_svg(columns, rows, validation)
    if fmt == "svg":
        destination.write_text(svg_content, encoding="utf-8")
        return {"svg_path": str(destination), "format": "svg", "columns": columns, "rows": rows, "validation": validation}

    converter = _resolve_svg_converter()
    if converter is None:
        raise ToolError(
            "no SVG-to-PNG converter found; install rsvg-convert (or ImageMagick/Inkscape) or set DIGITAL_SVG_CONVERTER"
        )
    handle, source_name = tempfile.mkstemp(prefix="digital-truth-table-", suffix=".svg")
    os.close(handle)
    source_svg = Path(source_name)
    try:
        source_svg.write_text(svg_content, encoding="utf-8")
        command = _convert_svg_to_png(source_svg, destination, converter, timeout, pixel_width)
        return {"image_path": str(destination), "format": "png", "columns": columns, "rows": rows, "validation": validation, "command": command}
    finally:
        source_svg.unlink(missing_ok=True)


def open_circuit(path: Path, app_path: str | None) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise ToolError("digital_open_circuit currently requires macOS")
    configured_app = os.environ.get("DIGITAL_APP")
    app = Path(app_path or configured_app).expanduser() if (app_path or configured_app) else ROOT / "modern/dist/Digital.app"
    if not app.exists():
        raise ToolError(f"Digital.app not found: {app}")
    # Do not execute Contents/MacOS/Digital from MCP. The jpackage native
    # launcher can abort in macOS HIServices/JRSAppKitAWT registration when it
    # is spawned by Python, even though the same app may open from Finder.
    # Launch the GUI through the verified external runtime instead.
    managed_java = ROOT / "modern/dist/runtime/bin/java"
    managed_jar = ROOT / "modern/dist/Digital.jar"
    if managed_java.is_file() and os.access(managed_java, os.X_OK) and managed_jar.is_file():
        env = os.environ.copy()
        env.setdefault("jna.library.path", str(ROOT / "modern/dist"))
        fallback = subprocess.Popen(
            [
                str(managed_java),
                "-Djava.awt.headless=false",
                "-Dfile.encoding=UTF-8",
                "-Djna.library.path=" + str(ROOT / "modern/dist"),
                "--add-opens=java.desktop/java.awt=ALL-UNNAMED",
                "--add-opens=java.desktop/sun.lwawt=ALL-UNNAMED",
                "--add-opens=java.desktop/sun.lwawt.macosx=ALL-UNNAMED",
                "--add-opens=java.desktop/java.awt.peer=ALL-UNNAMED",
                "-cp",
                str(ROOT / "modern/dist/*"),
                "de.neemann.digital.gui.Main",
                str(path),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.4)
        if fallback.poll() is None:
            return {"opened": True, "app": str(app), "path": str(path), "launcher": "managed-runtime"}
    raise ToolError(
        f"Digital GUI runtime is unavailable; refusing to launch the app bundle directly to avoid macOS AWT aborts: {app}"
    )


def load_circuit(path: Path, app_path: str | None) -> dict[str, Any]:
    """Load a circuit into Digital.app; the modern naming of open_circuit."""
    result = open_circuit(path, app_path)
    return {"loaded": True, "app": result["app"], "path": result["path"], "launcher": result["launcher"]}


def _read_properties(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
                if "=" in line and not line.startswith("#"))


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _live_session(session_id: Any) -> tuple[Path, dict[str, str]]:
    if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise ToolError("session_id must be a live session id returned by digital_live_start")
    root = (GENERATED / ".live").resolve()
    directory = (root / session_id).resolve()
    if directory.parent != root:
        raise ToolError("live session path must stay inside the session directory")
    state = _read_properties(directory / "state.properties")
    if not state:
        raise ToolError("live session does not exist")
    if (directory / "stop").exists():
        raise ToolError(f"live session was stopped; draft remains in {directory}")
    if state.get("state") != "building":
        raise ToolError(f"live session is already {state.get('state')}")
    viewer = _read_properties(directory / "viewer.properties")
    if viewer.get("state") in {"error", "closed", "stopped"}:
        raise ToolError(f"live viewer {viewer['state']}: {viewer.get('message', '')}; draft: {directory}")
    return directory, state


def _validate_live_design(design: dict[str, Any]) -> None:
    elements, wires = design["elements"], design["wires"]
    if not all(isinstance(element, dict) for element in elements):
        raise ToolError("each live element must be an object")
    for element in elements:
        if "inputs" in element:
            _positive_int(element["inputs"], "element.inputs", 2)
    for wire in wires:
        if not isinstance(wire, dict):
            raise ToolError("each live wire must be an object")
        for endpoint in (wire.get("p1"), wire.get("p2"), wire.get("from"), wire.get("to")):
            if isinstance(endpoint, dict):
                for axis in ("x", "y"):
                    value = endpoint.get(axis)
                    coordinate = _number(value, f"wire point.{axis}")
                    if coordinate != value or coordinate % 20:
                        raise ToolError("live wire points must be on the 20-unit grid")
        if isinstance(wire.get("to"), str):
            index = wire.get("input", 0)
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ToolError("wire input must be a non-negative integer")
            target = next((e for e in elements if e.get("id") == wire["to"]), None)
            if target:
                count = 1 if target.get("type") in {"Out", "Probe", "Not", "Buffer"} else target.get("inputs", 2)
                if not isinstance(count, int) or index >= count:
                    raise ToolError("wire input is outside the gate's input range")


def _publish_live(directory: Path, design: dict[str, Any], revision: int,
                  output: Path, complete: bool = False) -> dict[str, Any]:
    _validate_live_design(design)
    snapshot = directory / f"revision-{revision:06d}.dig"
    temporary = snapshot.with_suffix(".tmp")
    try:
        circuit = build_circuit(design, temporary)
    except (AttributeError, TypeError, KeyError, StopIteration) as exc:
        raise ToolError(f"invalid live design: {exc}") from exc
    lines: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for wire in circuit["wires"]:
        p1, p2 = ((wire[p]["x"], wire[p]["y"]) for p in ("p1", "p2"))
        if p1 == p2 or (p1[0] != p2[0] and p1[1] != p2[1]):
            raise ToolError("live wires must be non-zero horizontal or vertical segments; use explicit bends")
        axis = 0 if p1[0] == p2[0] else 1
        interval = tuple(sorted((p1[1 - axis], p2[1 - axis])))
        lines.setdefault((axis, p1[axis]), []).append(interval)
    for intervals in lines.values():
        ordered = sorted(intervals)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            raise ToolError("live wires must not contain duplicate or overlapping segments")
    os.replace(temporary, snapshot)
    _atomic_text(directory / f"design-{revision:06d}.json", json.dumps(design, ensure_ascii=False, allow_nan=False))
    message = f"Building circuit: {circuit['element_count']} components, {circuit['wire_count']} wires"
    result: dict[str, Any] = {"session_id": directory.name, "revision": revision,
                              "element_count": circuit["element_count"], "wire_count": circuit["wire_count"],
                              "draft_path": str(snapshot), "path": str(output),
                              "state": "complete" if complete else "building"}
    if complete:
        if output.exists():
            raise ToolError(f"output already exists: {output}; choose another file_name at session start")
        if design.get("tests"):
            try:
                result["tests"] = run_tests(snapshot, 30, True)
            except (ToolError, OSError) as exc:
                result["tests"] = {"passed": False, "error": str(exc)}
        message = "Complete"
        if "tests" in result:
            message += "; tests " + ("PASS" if result["tests"]["passed"] else "FAIL")
        if (directory / "stop").exists():
            raise ToolError(f"live session was stopped; draft remains in {directory}")
        created = False
        try:
            data = snapshot.read_bytes()
            with output.open("xb") as file:
                created = True
                file.write(data)
        except FileExistsError as exc:
            raise ToolError(f"output already exists: {output}") from exc
        except OSError:
            if created:
                output.unlink(missing_ok=True)
            raise
    if (directory / "stop").exists():
        raise ToolError(f"live session was stopped; draft remains in {directory}")
    # ponytail: immutable revisions plus one atomic pointer keep the viewer off partial files.
    _atomic_text(directory / "state.properties", "\n".join([
        f"revision={revision}", f"snapshot={snapshot.name}", f"state={result['state']}",
        f"output_uri={output.as_uri()}", f"message={message}", ""]))
    result["viewer"] = _read_properties(directory / "viewer.properties")
    return result


def _launch_live(directory: Path) -> None:
    log = directory / "viewer.log"
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([_java(), "--class-path", str(_jar().resolve()),
                                    str(SERVER_ROOT / "digital_mcp_live" / "LiveDigital.java"), str(directory.resolve())],
                                   cwd=directory, stdout=stream, stderr=stream, start_new_session=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        viewer = _read_properties(directory / "viewer.properties")
        if viewer.get("state") in {"ready", "building"}:
            threading.Thread(target=process.wait, daemon=True).start()
            return
        if process.poll() is not None or viewer.get("state") in {"error", "closed"}:
            break
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    detail = log.read_text(encoding="utf-8", errors="replace")[-2500:]
    raise ToolError(f"Live Digital could not start. Use a desktop JDK 17+ and DIGITAL_JAR. Draft: {directory}. {detail}")


def live_start(file_name: Any = None) -> dict[str, Any]:
    if file_name is not None and not isinstance(file_name, str):
        raise ToolError("file_name must be a simple .dig filename")
    output = _safe_output(file_name)
    if output.exists() or output.is_symlink():
        raise ToolError(f"output already exists: {output}")
    output = output.resolve()
    directory = GENERATED / ".live" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    result = _publish_live(directory, {"elements": [], "wires": []}, 0, output)
    try:
        _launch_live(directory)
    except (OSError, ToolError) as exc:
        (directory / "stop").touch()
        raise ToolError(f"{exc}; saved draft: {result['draft_path']}") from exc
    result["viewer"] = _read_properties(directory / "viewer.properties")
    return result


def live_update(session_id: Any, elements: Any = None, wires: Any = None,
                finish: bool = False, tests: Any = None) -> dict[str, Any]:
    directory, state = _live_session(session_id)
    revision = int(state["revision"])
    design = json.loads((directory / f"design-{revision:06d}.json").read_text(encoding="utf-8"))
    if finish:
        if tests is not None:
            if not isinstance(tests, list) or not all(isinstance(test, dict) for test in tests):
                raise ToolError("tests must be an array of test cases")
            design["tests"] = tests
    else:
        if not isinstance(elements, list) or not isinstance(wires, list) or not (elements or wires):
            raise ToolError("append needs a non-empty elements or wires array")
        design["elements"] += elements
        design["wires"] += wires
    uri = urlsplit(state["output_uri"])
    output = Path(unquote(uri.path).lstrip("/") if os.name == "nt" else unquote(uri.path))
    if output.resolve().parent != GENERATED.resolve() or uri.scheme != "file":
        raise ToolError("live output must stay inside the generated directory")
    return _publish_live(directory, design, revision + 1, output, complete=finish)


TOOLS = [
    {"name": "digital_live_start", "description": "Start a new circuit in a live Digital window BEFORE planning the entire design. Requires a desktop JDK 17+ and DIGITAL_JAR. Then call digital_live_append repeatedly as you design small groups of gates and wires. Do not wait for the complete design. Existing files are never overwritten.", "inputSchema": {"type": "object", "properties": {"file_name": {"type": "string", "description": "New simple .dig filename in the output directory."}}}},
    {"name": "digital_live_append", "description": "Add a small batch to a live circuit immediately, while continuing to design. Use the existing design elements/wires format, unique element IDs, 20-unit grid and orthogonal explicit bends. Enter gate inputs horizontally from the left, with bends at least 40 units before the pin; keep branches separate and never run wires vertically along a gate's input edge. Only reference components already present or in this batch. Append-only: no move/delete/replace. The user sees updates before finish; return to this tool frequently rather than preparing the whole design first.", "inputSchema": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}, "elements": {"type": "array", "items": {"type": "object"}, "default": []}, "wires": {"type": "array", "items": {"type": "object"}, "default": []}}}},
    {"name": "digital_live_finish", "description": "Finish a live circuit, save the final .dig without overwriting existing files and unlock the same Digital window for editing. Optional embedded tests are run before completion and report PASS/FAIL; incomplete circuits are never auto-simulated.", "inputSchema": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}, "tests": {"type": "array", "items": {"type": "object"}}}}},
    {"name": "digital_build_circuit", "description": "Build a Digital .dig file from a structured circuit design, optionally test, render and open it. The host AI should translate text or an attached image into this design object.", "inputSchema": {"type": "object", "required": ["design"], "properties": {"design": {"type": "object", "description": "Object with elements, wires, and optional tests; see digital_design_schema."}, "file_name": {"type": "string", "description": "Optional simple .dig filename."}, "run_tests": {"type": "boolean", "description": "Run embedded test cases after building."}, "render_svg": {"type": "boolean", "description": "Export an SVG preview after building."}, "open": {"type": "boolean", "description": "Open the generated circuit in Digital.app on macOS."}, "timeout_seconds": {"type": "integer", "default": 30}}}},
    {"name": "digital_inspect_circuit", "description": "Read a Digital .dig file and return its elements, positions, attributes and wires.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}},
    {"name": "digital_run_tests", "description": "Run the test cases embedded in a Digital .dig file through Digital's headless CLI.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 30}, "verbose": {"type": "boolean", "default": True}}}},
    {"name": "digital_render_circuit", "description": "Render a Digital .dig file using Digital's CLI. Kept for compatibility; use digital_export_rendered_image for the explicit SVG/PNG interface.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "format": {"type": "string", "enum": ["svg", "png"], "default": "svg"}, "output_path": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 30}}}},
    {"name": "digital_open_circuit", "description": "Open a .dig file in the modern Digital.app on macOS.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "app_path": {"type": "string"}}}},
    {"name": "digital_load_circuit", "description": "Load a .dig file into the modern Digital.app on macOS using the safest available launcher; returns loaded, app, path and launcher.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "app_path": {"type": "string"}}}},
    {"name": "digital_export_rendered_image", "description": "Export a rendered image of a .dig circuit. format=svg reuses Digital's CLI SVG export; format=png (default) generates the SVG first, then converts it at the requested pixel width with DIGITAL_SVG_CONVERTER or rsvg-convert/magick/convert/inkscape from PATH.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "format": {"type": "string", "enum": ["svg", "png"], "default": "png"}, "output_path": {"type": "string"}, "pixel_width": {"type": "integer", "default": 2048}, "timeout_seconds": {"type": "integer", "default": 30}}}},
    {"name": "digital_export_simulation_image", "description": "Apply input/button states such as {A: 1, B: 0}, render the live Digital model, and export a high-resolution SVG or PNG snapshot. The circuit is simulated headlessly; the GUI is not driven.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "inputs": {"type": "object", "additionalProperties": {"oneOf": [{"type": "integer"}, {"type": "boolean"}, {"type": "string"}]}}, "format": {"type": "string", "enum": ["svg", "png"], "default": "png"}, "output_path": {"type": "string"}, "pixel_width": {"type": "integer", "default": 2048}, "scale": {"type": "integer", "default": 15}, "hide_test": {"type": "boolean", "default": True}, "timeout_seconds": {"type": "integer", "default": 30}}}},
    {"name": "digital_export_truth_table_image", "description": "Read embedded Digital Testcase data, validate it with Digital's simulator, and export a polished truth-table/results image as SVG or high-resolution PNG.", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "format": {"type": "string", "enum": ["svg", "png"], "default": "png"}, "output_path": {"type": "string"}, "pixel_width": {"type": "integer", "default": 2048}, "timeout_seconds": {"type": "integer", "default": 30}}}},
    {"name": "digital_design_schema", "description": "Return the design schema, common gate pin conventions and checked circuit_reference entries from Brown/Vranesic, Fundamentals of Digital Logic with VHDL Design, third edition. Read the matching reference before designing a circuit, cite its figure/page and distinguish book behavior from adaptations.", "inputSchema": {"type": "object", "properties": {}}},
]


SCHEMA = {"elements": [{"id": "a", "type": "In", "label": "A", "x": 200, "y": 100}, {"id": "b", "type": "In", "label": "B", "x": 200, "y": 140}, {"id": "and1", "type": "And", "x": 240, "y": 100}, {"id": "y", "type": "Out", "label": "Y", "x": 340, "y": 120}], "wires": [{"from": "a", "to": "and1", "input": 0}, {"from": "b", "to": "and1", "input": 1}, {"from": "and1", "to": "y"}], "tests": [{"inputs": {"A": 0, "B": 0}, "outputs": {"Y": 0}}, {"inputs": {"A": 1, "B": 1}, "outputs": {"Y": 1}}], "supported_types": ["In", "Out", "And", "Or", "XOr", "XNOr", "NAnd", "NOr", "Not", "Clock", "Const", "Ground", "VDD", "Testcase"], "wire_note": "For uncommon elements, use explicit p1/p2 coordinates. Common gate inputs are spaced 40 units vertically."}


SCHEMA["element_grid"] = {"step": 20, "note": "Element x/y must be integer multiples of 20 to preserve wires when Digital copies or edits the circuit."}

SCHEMA["circuit_reference"] = {
    "title": "Fundamentals of Digital Logic with VHDL Design",
    "authors": ["Stephen Brown", "Zvonko Vranesic"],
    "edition": 3,
    "publisher": "McGraw-Hill",
    "year": 2009,
    "isbn": "978-0-07-352953-0",
    "source_pdf": "Fundamentals-of-Digital-Logic-with-VHDL-Design-3rd-edition.pdf",
    "page_note": "Printed pages identify the book; one-based PDF pages apply only to the checked 961-page copy. The PDF is not bundled or read at runtime.",
    "entries": {
        "d_latch": {
            "section": "7.3", "figure": "7.8", "printed_page": 389, "pdf_page": 410,
            "behavior": "Level-sensitive: Clk=1 follows D; Clk=0 holds Q.",
            "structure": "Inverter, two NAND input gates, cross-coupled NAND latch.",
        },
        "master_slave_d": {
            "section": "7.4.1", "figure": "7.10", "printed_page": 392, "pdf_page": 413,
            "behavior": "Falling-edge output: master transparent high, slave transparent low.",
            "structure": "Two gated D latches on opposite clock phases; use figure 7.8 when expanding the latch blocks into gates.",
        },
        "t_flip_flop": {
            "section": "7.5", "figure": "7.16", "printed_page": 399, "pdf_page": 420,
            "behavior": "At the rising edge, T=0 holds Q and T=1 toggles Q.",
            "structure": "Rising-edge D flip-flop with D = NOT(T) AND Q OR T AND NOT(Q).",
            "adaptation": "D = T XOR Q is equivalent, but the book figure uses AND/OR gates and an inverter. Using the falling-edge master-slave design changes the active edge unless adapted.",
        },
        "jk_flip_flop": {
            "section": "7.6", "figure": "7.17", "printed_page": 400, "pdf_page": 421,
            "behavior": "At the rising edge, JK=00 holds, 01 resets, 10 sets, 11 toggles.",
            "structure": "Rising-edge D flip-flop with D = J AND NOT(Q) OR NOT(K) AND Q.",
        },
    },
    "agent_rules": [
        "Prefer these checked book references for matching circuits; cite authors, edition, section, figure and printed page in the result explanation.",
        "These are verified summaries, not runtime access to the book. Inspect the local PDF before claiming direct diagram inspection or citing additional topics.",
        "State the active clock edge or enable polarity and label every equivalent gate substitution, clock inversion or reset addition as an adaptation.",
        "Preserve Digital grid/pin/wire layout conventions and test transparency, hold, sampling/toggle, initialization and non-active-edge behavior as appropriate.",
    ],
}


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "digital_live_start":
        return live_start(args.get("file_name"))
    if name == "digital_live_append":
        return live_update(args.get("session_id"), args.get("elements", []), args.get("wires", []))
    if name == "digital_live_finish":
        return live_update(args.get("session_id"), finish=True, tests=args.get("tests"))
    if name == "digital_design_schema":
        return SCHEMA
    if name == "digital_build_circuit":
        output = _safe_output(args.get("file_name"))
        result = build_circuit(args.get("design"), output)
        timeout = max(1, int(args.get("timeout_seconds", 30)))
        if args.get("run_tests"):
            result["tests"] = run_tests(output, timeout, True)
        if args.get("render_svg"):
            result["render"] = render_svg(output, timeout)
        if args.get("open"):
            result["open"] = open_circuit(output, args.get("app_path"))
        return result
    path = _input_path(args.get("path"), ".dig")
    if name == "digital_inspect_circuit":
        return inspect_circuit(path)
    if name == "digital_run_tests":
        return run_tests(path, max(1, int(args.get("timeout_seconds", 30))), bool(args.get("verbose", True)))
    if name in ("digital_render_circuit", "digital_export_rendered_image"):
        default_format = "svg" if name == "digital_render_circuit" else "png"
        fmt = str(args.get("format", default_format)).lower()
        if fmt not in ("svg", "png"):
            raise ToolError("format must be 'svg' or 'png'")
        timeout = max(1, int(args.get("timeout_seconds", 30)))
        output_path = args.get("output_path")
        if name == "digital_render_circuit" and fmt == "svg" and output_path is None:
            return render_svg(path, timeout)  # legacy shape for the original tool
        if fmt == "svg":
            return _export_svg(path, output_path, timeout)
        return _export_png(path, output_path, timeout, _positive_int(args.get("pixel_width"), "pixel_width", 2048))
    if name == "digital_export_simulation_image":
        fmt = str(args.get("format", "png")).lower()
        if fmt not in ("svg", "png"):
            raise ToolError("format must be 'svg' or 'png'")
        timeout = max(1, int(args.get("timeout_seconds", 30)))
        return _export_simulation_image(
            path,
            args.get("output_path"),
            fmt,
            args.get("inputs"),
            timeout,
            _positive_int(args.get("pixel_width"), "pixel_width", 2048),
            _positive_int(args.get("scale"), "scale", 15),
            bool(args.get("hide_test", True)),
        )
    if name == "digital_export_truth_table_image":
        fmt = str(args.get("format", "png")).lower()
        if fmt not in ("svg", "png"):
            raise ToolError("format must be 'svg' or 'png'")
        timeout = max(1, int(args.get("timeout_seconds", 30)))
        return _export_truth_table_image(
            path,
            args.get("output_path"),
            fmt,
            timeout,
            _positive_int(args.get("pixel_width"), "pixel_width", 2048),
        )
    if name == "digital_open_circuit":
        return open_circuit(path, args.get("app_path"))
    if name == "digital_load_circuit":
        return load_circuit(path, args.get("app_path"))
    raise ToolError(f"unknown tool: {name}")


def response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> None:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    # stdio MCP uses one complete JSON-RPC message per line. Keep the human-
    # readable indentation inside tool text, but never pretty-print the
    # protocol envelope itself across multiple lines.
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                response(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "digital-mcp", "version": "0.1.0"}, "instructions": "For circuit designs, call digital_design_schema and consult circuit_reference. Prefer the checked references from Brown and Vranesic, Fundamentals of Digital Logic with VHDL Design, third edition, and cite section, figure and printed page in your explanation. State the active clock edge and label adaptations. Do not claim runtime PDF access or invent references outside the checked entries. For live circuit creation, call digital_live_start early, then digital_live_append frequently with small groups of components and orthogonal wires as you design. Do not prepare the complete circuit before starting. Finally call digital_live_finish with meaningful tests when available. The user watches a read-only draft and edits after finish. Never describe an untested circuit as validated."})
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                response(request_id, {})
            elif method == "tools/list":
                response(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = request.get("params", {})
                try:
                    result = call_tool(params.get("name"), params.get("arguments", {}))
                    response(request_id, {"content": [{"type": "text", "text": _json(result)}], "structuredContent": result, "isError": False})
                except (ToolError, ValueError, OSError) as exc:
                    response(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            else:
                response(request_id, error={"code": -32601, "message": f"method not found: {method}"})
        except json.JSONDecodeError as exc:
            response(None, error={"code": -32700, "message": str(exc)})
        except Exception as exc:  # keep the stdio server alive for the next request
            response(request.get("id") if isinstance(request, dict) else None, error={"code": -32603, "message": str(exc)})


if __name__ == "__main__":
    main()
