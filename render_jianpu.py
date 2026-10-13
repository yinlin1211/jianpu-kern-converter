#!/usr/bin/env python3
"""Render Jianpu notation through the original HC notation -> SVG path.

The HC engine is third-party and is intentionally not bundled. Set HC_ENGINE
or pass --engine with the path to a local hc_*.js file.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HC_WRAPPER = ROOT / "tools" / "render_hc.js"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def load_notation(path: str | Path) -> tuple[str, str]:
    """Read a notation text file or a raw JSON/songdata JSON file."""
    source = Path(path)
    if source.suffix.lower() != ".json":
        return source.read_text(encoding="utf-8"), source.stem

    data = json.loads(source.read_text(encoding="utf-8"))
    songdata = data.get("songdata", data)
    if isinstance(songdata, dict) and isinstance(songdata.get("notation"), str):
        return songdata["notation"], songdata.get("song_name") or source.stem
    raise ValueError(f"{source}: JSON does not contain songdata.notation")


def render_notation(
    notation_path: str | Path,
    out_path: str | Path,
    *,
    engine: str = "",
    title: str = "",
) -> None:
    """Invoke the local HC engine wrapper and write the original SVG."""
    notation = Path(notation_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["node", str(HC_WRAPPER), "--notation", str(notation), "--out", str(out)]
    if engine:
        cmd += ["--engine", engine]
    if title:
        cmd += ["--title", title]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"HC rendering failed for {notation}: {detail[-3000:]}")
    print(result.stdout.strip())


def render_json(
    json_path: str | Path,
    out_path: str | Path,
    *,
    engine: str = "",
    title: str = "",
) -> None:
    """Render a raw songdata JSON, including its optional lyric field."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["node", str(HC_WRAPPER), "--json", str(json_path), "--out", str(out)]
    if engine:
        cmd += ["--engine", engine]
    if title:
        cmd += ["--title", title]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"HC rendering failed for {json_path}: {detail[-3000:]}")
    print(result.stdout.strip())


def _svg_size(root: ET.Element) -> tuple[float, float]:
    view_box = root.get("viewBox", "").split()
    if len(view_box) == 4:
        return float(view_box[2]), float(view_box[3])

    def numeric(value: str | None, fallback: float) -> float:
        match = re.match(r"\s*([0-9.]+)", value or "")
        return float(match.group(1)) if match else fallback

    return numeric(root.get("width"), 1000.0), numeric(root.get("height"), 1414.0)


def render_comparison_svg(
    before_path: str | Path,
    after_path: str | Path,
    out_path: str | Path,
    *,
    title: str = "",
) -> None:
    """Stack two original HC SVG pages into one inspectable comparison SVG."""
    before = ET.parse(before_path).getroot()
    after = ET.parse(after_path).getroot()
    before_w, before_h = _svg_size(before)
    after_w, after_h = _svg_size(after)
    width = max(before_w, after_w)
    label_h = 36.0
    gap = 18.0
    top = label_h if title else 0.0
    height = top + label_h + before_h + gap + label_h + after_h

    root = ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "width": f"{width:g}",
            "height": f"{height:g}",
            "viewBox": f"0 0 {width:g} {height:g}",
            "class": "hc-comparison",
            "version": "1.1",
        },
    )
    root.append(
        ET.Element(
            f"{{{SVG_NS}}}rect",
            {"width": "100%", "height": "100%", "fill": "white"},
        )
    )
    if title:
        title_node = ET.Element(
            f"{{{SVG_NS}}}text",
            {
                "x": "20",
                "y": "24",
                "font-family": "sans-serif",
                "font-size": "16",
                "font-weight": "bold",
            },
        )
        title_node.text = title
        root.append(title_node)

    def add_page(source: ET.Element, y: float, label: str) -> None:
        text = ET.Element(
            f"{{{SVG_NS}}}text",
            {
                "x": "20",
                "y": f"{y + 24:g}",
                "font-family": "sans-serif",
                "font-size": "14",
                "font-weight": "bold",
            },
        )
        text.text = label
        root.append(text)
        group = ET.Element(
            f"{{{SVG_NS}}}g",
            {"transform": f"translate(0,{y + label_h:g})"},
        )
        for child in source:
            group.append(copy.deepcopy(child))
        root.append(group)

    first_y = top
    add_page(before, first_y, "BEFORE: original Jianpu notation")
    second_y = first_y + label_h + before_h + gap
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    add_page(after, second_y, "AFTER: Kern roundtrip notation")
    Path(out_path).write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def main() -> None:
    ap = argparse.ArgumentParser(description="Render original Jianpu notation SVGs through HC")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--notation", help="notation text file")
    source.add_argument("--json", "--raw", dest="json_path", help="JSON containing songdata.notation")
    ap.add_argument("--out", required=True, help="output SVG path")
    ap.add_argument("--engine", default=os.environ.get("HC_ENGINE", ""))
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    if not args.engine:
        ap.error("set HC_ENGINE or pass --engine /path/to/hc_*.js")
    if args.notation:
        render_notation(args.notation, args.out, engine=args.engine, title=args.title)
    else:
        render_json(args.json_path, args.out, engine=args.engine, title=args.title)


if __name__ == "__main__":
    main()
