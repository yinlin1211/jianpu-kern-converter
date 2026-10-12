#!/usr/bin/env python3
"""Render event timelines as small, dependency-free SVG score comparisons.

This is a semantic comparison renderer. It deliberately renders the event
timeline rather than claiming to reproduce a source editor's typography.
"""
from __future__ import annotations

import argparse
import html
import json
from fractions import Fraction
from pathlib import Path

from jianpu_kern import kern_to_timeline


def _frac(value) -> Fraction:
    return Fraction(str(value))


def load_events(path: str | Path) -> tuple[str, int, list[tuple[Fraction, Fraction, int]]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    events = [
        (_frac(row["onset"]), _frac(row["dur"]), int(row["midi"]))
        for row in data["events"]
    ]
    return data.get("title", Path(path).stem), int(data.get("meter_bpb", 4)), events


def pitch_label(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


def jianpu_label(midi: int, tonic: int = 60) -> str:
    names = ["1", "#1", "2", "#2", "3", "4", "#4", "5", "#5", "6", "#6", "7"]
    delta = midi - tonic
    octave, degree = divmod(delta, 12)
    label = names[degree]
    if octave > 0:
        label += "'" * octave
    elif octave < 0:
        label += "," * (-octave)
    return label


def _text(x: float, y: float, value: str, size: int = 14, weight: str = "400") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="DejaVu Sans, sans-serif" '
        f'font-size="{size}px" font-weight="{weight}" fill="#17212b">'
        f"{html.escape(value)}</text>"
    )


def render_events_svg(
    events: list[tuple[Fraction, Fraction, int]],
    title: str,
    out: str | Path,
    meter_bpb: int = 4,
    tonic: int = 60,
) -> None:
    """Render a timeline with notes, rests, barlines, and exact values."""
    if not events:
        raise ValueError("cannot render an empty event list")

    end = max(onset + dur for onset, dur, _ in events)
    bars = max(1, (int(end / meter_bpb) + (end % meter_bpb != 0)))
    total_beats = Fraction(bars * meter_bpb)
    scale = 112.0
    left, top = 54.0, 88.0
    row_h = 112.0
    width = left + float(total_beats) * scale + 44.0
    height = top + row_h + 56.0
    baseline = top + 66.0

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">',
        "<rect width=\"100%\" height=\"100%\" fill=\"#fbfaf7\"/>",
        _text(left, 32, title, 21, "700"),
        _text(left, 56, "semantic event timeline | blue = note | gray = rest", 12),
        f'<line x1="{left}" y1="{baseline}" x2="{width - 32}" y2="{baseline}" '
        'stroke="#c7cdd2" stroke-width="1"/>',
    ]

    for bar in range(bars + 1):
        x = left + bar * meter_bpb * scale
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 14:.1f}" x2="{x:.1f}" '
            f'y2="{baseline + 18:.1f}" stroke="#697681" stroke-width="2"/>'
        )
        if bar < bars:
            parts.append(_text(x + 5, top - 22, f"bar {bar + 1}", 11))

    previous_end = Fraction(0)
    for index, (onset, dur, midi) in enumerate(events):
        if onset > previous_end:
            x = left + float(previous_end) * scale
            w = float(onset - previous_end) * scale
            parts.append(
                f'<rect x="{x:.1f}" y="{baseline - 22:.1f}" width="{w:.1f}" height="44" '
                'rx="4" fill="#e5e8eb" stroke="#b7c0c7"/>'
            )
            parts.append(_text(x + 6, baseline + 5, f"rest {onset - previous_end}", 11))

        x = left + float(onset) * scale
        w = max(12.0, float(dur) * scale)
        parts.append(
            f'<rect x="{x:.1f}" y="{baseline - 34:.1f}" width="{w:.1f}" height="68" '
            'rx="6" fill="#b9ddf5" stroke="#2674a8" stroke-width="1.5"/>'
        )
        parts.append(_text(x + 7, baseline - 8, jianpu_label(midi, tonic), 18, "700"))
        parts.append(_text(x + 7, baseline + 14, pitch_label(midi), 11))
        parts.append(_text(x + 7, baseline + 29, f"#{index}: {onset}+{dur}", 10))
        previous_end = max(previous_end, onset + dur)

    if previous_end < total_beats:
        x = left + float(previous_end) * scale
        w = float(total_beats - previous_end) * scale
        parts.append(
            f'<rect x="{x:.1f}" y="{baseline - 22:.1f}" width="{w:.1f}" height="44" '
            'rx="4" fill="#f0f1f2" stroke="#d0d4d7"/>'
        )

    parts.append("</svg>")
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def render_comparison_svg(
    source: list[tuple[Fraction, Fraction, int]],
    recovered: list[tuple[Fraction, Fraction, int]],
    title: str,
    out: str | Path,
    meter_bpb: int = 4,
    tonic: int = 60,
) -> None:
    """Render source and recovered timelines in one SVG for visual comparison."""
    if not source or not recovered:
        raise ValueError("comparison requires two non-empty event lists")

    end = max(
        max(onset + dur for onset, dur, _ in source),
        max(onset + dur for onset, dur, _ in recovered),
    )
    bars = max(1, int(end / meter_bpb) + bool(end % meter_bpb))
    total_beats = Fraction(bars * meter_bpb)
    scale = 112.0
    left, top = 54.0, 92.0
    row_h = 122.0
    width = left + float(total_beats) * scale + 44.0
    height = top + row_h * 2 + 42.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">',
        "<rect width=\"100%\" height=\"100%\" fill=\"#fbfaf7\"/>",
        _text(left, 32, title, 21, "700"),
        _text(left, 56, "source events vs Kern roundtrip | exact Fraction timeline", 12),
    ]

    for bar in range(bars + 1):
        x = left + bar * meter_bpb * scale
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 20:.1f}" x2="{x:.1f}" '
            f'y2="{top + row_h * 2 - 18:.1f}" stroke="#697681" stroke-width="2"/>'
        )
        if bar < bars:
            parts.append(_text(x + 5, top - 28, f"bar {bar + 1}", 11))

    def draw_row(
        events: list[tuple[Fraction, Fraction, int]], label: str, row: int, fill: str
    ) -> None:
        baseline = top + row * row_h + 60.0
        parts.append(_text(8, baseline + 5, label, 12, "700"))
        parts.append(
            f'<line x1="{left}" y1="{baseline:.1f}" x2="{width - 32:.1f}" '
            f'y2="{baseline:.1f}" stroke="#c7cdd2" stroke-width="1"/>'
        )
        previous_end = Fraction(0)
        for index, (onset, dur, midi) in enumerate(events):
            if onset > previous_end:
                x = left + float(previous_end) * scale
                w = float(onset - previous_end) * scale
                parts.append(
                    f'<rect x="{x:.1f}" y="{baseline - 18:.1f}" width="{w:.1f}" height="36" '
                    'rx="4" fill="#e5e8eb" stroke="#b7c0c7"/>'
                )
            x = left + float(onset) * scale
            w = max(12.0, float(dur) * scale)
            parts.append(
                f'<rect x="{x:.1f}" y="{baseline - 30:.1f}" width="{w:.1f}" height="60" '
                f'rx="6" fill="{fill}" stroke="#2674a8" stroke-width="1.5"/>'
            )
            parts.append(_text(x + 7, baseline - 5, jianpu_label(midi, tonic), 16, "700"))
            parts.append(_text(x + 7, baseline + 14, f"#{index} {onset}+{dur}", 10))
            previous_end = max(previous_end, onset + dur)

    draw_row(source, "source", 0, "#b9ddf5")
    draw_row(recovered, "after", 1, "#c8e8c5")
    parts.append("</svg>")
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Render Jianpu event timelines as SVG")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--events", help="JSON with title, meter_bpb, and events")
    src.add_argument("--kern", help="single-spine **kern file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--meter-bpb", type=int, default=4)
    args = ap.parse_args()

    if args.events:
        title, meter, events = load_events(args.events)
        render_events_svg(events, args.title or title, args.out, meter)
        return

    kern = Path(args.kern).read_text(encoding="utf-8")
    events = kern_to_timeline(kern)
    render_events_svg(events, args.title or Path(args.kern).stem, args.out, args.meter_bpb)


if __name__ == "__main__":
    main()
