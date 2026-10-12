#!/usr/bin/env python3
"""Build ten small, synthetic, public-domain conversion examples."""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jianpu_kern import jianpu_to_kern, kern_to_timeline  # noqa: E402
from render_jianpu import render_comparison_svg, render_events_svg  # noqa: E402


def F(value: str) -> Fraction:
    return Fraction(value)


CASES = [
    (
        "01_basic_quarters",
        "Basic quarter-note grid",
        "Regular quarter-note melody on the 4/4 grid.",
        [("0", "1", 60), ("1", "1", 62), ("2", "1", 64), ("3", "1", 65)],
    ),
    (
        "02_internal_rests",
        "Internal rests",
        "Gaps inside a bar are emitted as explicit Kern rests.",
        [("0", "1/2", 60), ("2", "1/2", 64), ("3", "1", 67)],
    ),
    (
        "03_pickup",
        "Pickup / anacrusis",
        "The first note starts at beat 3, so the leading rest preserves phase.",
        [("3", "1", 67), ("4", "1", 69), ("5", "1", 71), ("6", "1", 72)],
    ),
    (
        "04_dotted_values",
        "Dotted values",
        "Dotted quarter and dotted eighth durations.",
        [("0", "3/2", 60), ("3/2", "3/4", 64), ("9/4", "1/4", 67)],
    ),
    (
        "05_cross_bar_tie",
        "Cross-bar tie",
        "A note crossing the 4/4 barline is split and tied in Kern.",
        [("3", "2", 64), ("5", "1", 67), ("6", "1", 69)],
    ),
    (
        "06_complex_25",
        "Complex 2.5-beat value",
        "A value without one-token Kern spelling is split exactly and tied.",
        [("3", "5/2", 60), ("11/2", "1/2", 64)],
    ),
    (
        "07_triplets",
        "Triplet timing",
        "Third-beat subdivisions stay exact in the Fraction timeline.",
        [("0", "1/3", 60), ("1/3", "1/3", 62), ("2/3", "1/3", 64), ("1", "1", 67)],
    ),
    (
        "08_chromatic_octaves",
        "Accidentals and octaves",
        "Chromatic pitches and multiple Kern octave letters.",
        [("0", "1", 48), ("1", "1", 61), ("2", "1", 73), ("3", "1", 84)],
    ),
    (
        "09_repeated_pitch",
        "Repeated pitch without a tie",
        "Adjacent events with the same pitch remain separate events.",
        [("0", "1/2", 64), ("1", "1/2", 64), ("2", "1", 64), ("3", "1", 62)],
    ),
    (
        "10_multi_bar_rest",
        "Multi-bar rest and late entry",
        "Long gaps are preserved across bar boundaries.",
        [("0", "1", 60), ("12", "1", 67), ("13", "1/2", 69)],
    ),
]


def encode_events(events):
    return [
        {"onset": str(onset), "dur": str(dur), "midi": midi}
        for onset, dur, midi in events
    ]


def main() -> None:
    out_dir = ROOT / "examples"
    out_dir.mkdir(exist_ok=True)
    manifest = []
    for slug, title, description, raw_events in CASES:
        events = [(F(o), F(d), midi) for o, d, midi in raw_events]
        kern = jianpu_to_kern(events, meter_bpb=4)
        recovered = kern_to_timeline(kern)
        if events != recovered:
            raise AssertionError(f"{slug}: roundtrip mismatch: {events!r} != {recovered!r}")

        payload = {"title": title, "meter_bpb": 4, "events": encode_events(events)}
        (out_dir / f"{slug}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / f"{slug}.krn").write_text(kern + "\n", encoding="utf-8")
        render_events_svg(events, f"{title} | source events", out_dir / f"{slug}_before.svg")
        render_events_svg(
            recovered,
            f"{title} | Kern roundtrip",
            out_dir / f"{slug}_after.svg",
        )
        render_comparison_svg(
            events,
            recovered,
            title,
            out_dir / f"{slug}_comparison.svg",
        )
        manifest.append(
            {
                "slug": slug,
                "title": title,
                "description": description,
                "events": len(events),
                "exact": True,
            }
        )

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated {len(manifest)} examples in {out_dir}")


if __name__ == "__main__":
    main()
