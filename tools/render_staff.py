#!/usr/bin/env python3
"""Render a single-spine Humdrum **kern file as standard staff SVG."""
from __future__ import annotations

import argparse
from pathlib import Path
from fractions import Fraction
from typing import Mapping, Sequence
import re

from jianpu_kern.from_kern import dur_of


def _terminated_kern(kern: str) -> str:
    """Add the required single-spine terminator for rendering only."""
    lines = kern.splitlines()
    if not lines:
        return kern
    if any(line.strip() == "*-" for line in lines):
        return kern
    return "\n".join([*lines, "*-"]) + "\n"


def _kern_with_lyrics(kern: str, lyrics: Sequence[str | None] | Mapping[Fraction, str | None]) -> str:
    """Add a Verovio text spine without changing the conversion output."""
    if not lyrics:
        return _terminated_kern(kern)

    lines = _terminated_kern(kern).splitlines()
    rendered: list[str] = []
    event_index = 0
    tied = False
    cursor = Fraction(0)
    for line in lines:
        token = line.strip()
        if token == "**kern":
            rendered.append("**kern\t**text")
        elif token == "*-":
            rendered.append("*-\t*-")
        elif token.startswith("="):
            rendered.append(f"{line}\t=")
        elif token.startswith("*"):
            rendered.append(f"{line}\t*")
        elif token.startswith("!"):
            rendered.append(f"{line}\t!")
        elif token:
            duration_match = re.match(r"^(\d+\.*)", token)
            if not duration_match:
                raise RuntimeError(f"Unsupported Kern data record: {token!r}")
            if isinstance(lyrics, Mapping):
                text = lyrics.get(cursor) or "."
                text = text.replace("\t", " ")
            elif token.endswith("r"):
                text = "."
                event_index += 1
            else:
                text = "."
                if not tied:
                    if event_index < len(lyrics):
                        lyric = lyrics[event_index]
                        text = (lyric or ".").replace("\t", " ")
                    event_index += 1
                if "]" in token:
                    tied = False
                elif "[" in token:
                    tied = True
            rendered.append(f"{line}\t{text}")
            cursor += dur_of(duration_match[1])
        else:
            rendered.append(f"{line}\t*")
    return "\n".join(rendered) + "\n"


def render_kern_svg(
    kern_path: str | Path,
    out_path: str | Path,
    *,
    page_width: int = 1400,
    scale: int = 45,
    lyrics: Sequence[str | None] | Mapping[Fraction, str | None] | None = None,
) -> str:
    try:
        import verovio
    except ImportError as exc:
        raise RuntimeError(
            "Staff SVG rendering requires the optional Python package `verovio`."
        ) from exc

    toolkit = verovio.toolkit()
    toolkit.setOptions(
        {
            "pageWidth": page_width,
            "scale": scale,
            "adjustPageHeight": True,
            "header": "none",
            "footer": "none",
        }
    )
    kern = Path(kern_path).read_text(encoding="utf-8")
    render_data = _kern_with_lyrics(kern, lyrics or ())
    if not toolkit.loadData(render_data):
        raise RuntimeError(f"Verovio could not parse {kern_path}")
    if toolkit.getPageCount() < 1:
        raise RuntimeError(f"Verovio produced no pages for {kern_path}")
    svg = toolkit.renderToSVG(1)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(svg, encoding="utf-8")
    return svg


def main() -> int:
    ap = argparse.ArgumentParser(description="Render **kern as staff SVG")
    ap.add_argument("kern")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--scale", type=int, default=45)
    args = ap.parse_args()
    svg = render_kern_svg(args.kern, args.out, page_width=args.width, scale=args.scale)
    print(f"{args.out} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
