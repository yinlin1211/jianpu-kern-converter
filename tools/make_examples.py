#!/usr/bin/env python3
"""Build ten public-domain Jianpu -> Kern -> Jianpu examples."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jianpu_kern import jianpu_to_kern, kern_to_timeline  # noqa: E402
from render_jianpu import render_comparison_svg, render_json  # noqa: E402


def F(value: str) -> Fraction:
    return Fraction(value)


CASES = [
    ("01_basic_quarters", "Basic quarter-note grid", "Regular quarter-note melody on the 4/4 grid.", [("0", "1", 60), ("1", "1", 62), ("2", "1", 64), ("3", "1", 65)]),
    ("02_internal_rests", "Internal rests", "Gaps inside a bar are emitted as explicit Kern rests.", [("0", "1/2", 60), ("2", "1/2", 64), ("3", "1", 67)]),
    ("03_pickup", "Pickup / anacrusis", "The first note starts at beat 3, so the leading phase is preserved.", [("3", "1", 67), ("4", "1", 69), ("5", "1", 71), ("6", "1", 72)]),
    ("04_dotted_values", "Dotted values", "Dotted quarter and dotted eighth durations.", [("0", "3/2", 60), ("3/2", "3/4", 64), ("9/4", "1/4", 67)]),
    ("05_cross_bar_tie", "Cross-bar tie", "A note crossing the 4/4 barline is split and grouped in notation and Kern.", [("3", "2", 64), ("5", "1", 67), ("6", "1", 69)]),
    ("06_complex_25", "Complex 2.5-beat value", "A value without one-token Kern spelling is split exactly and tied.", [("3", "5/2", 60), ("11/2", "1/2", 64)]),
    ("07_triplets", "Triplet timing", "Third-beat subdivisions stay exact in the Fraction timeline.", [("0", "1/3", 60), ("1/3", "1/3", 62), ("2/3", "1/3", 64), ("1", "1", 67)]),
    ("08_chromatic_octaves", "Accidentals and octaves", "Chromatic pitches and multiple Jianpu octave marks.", [("0", "1", 48), ("1", "1", 61), ("2", "1", 73), ("3", "1", 84)]),
    ("09_repeated_pitch", "Repeated pitch without a tie", "Adjacent events with the same pitch remain separate events.", [("0", "1/2", 64), ("1", "1/2", 64), ("2", "1", 64), ("3", "1", 62)]),
    ("10_multi_bar_rest", "Multi-bar rest and late entry", "Long gaps are preserved across bar boundaries.", [("0", "1", 60), ("12", "1", 67), ("13", "1/2", 69)]),
]

DEGREE_NAMES = {
    0: ("1", ""), 1: ("1", "#"), 2: ("2", ""), 3: ("2", "#"),
    4: ("3", ""), 5: ("4", ""), 6: ("4", "#"), 7: ("5", ""),
    8: ("5", "#"), 9: ("6", ""), 10: ("6", "#"), 11: ("7", ""),
}


def pitch_head(midi: int, tonic: int = 60) -> str:
    relative = midi - tonic
    degree, accidental = DEGREE_NAMES[relative % 12]
    octave = (relative - (relative % 12)) // 12
    return degree + accidental + ("g" * octave if octave > 0 else "," * -octave)


def suffix(duration: Fraction) -> str | None:
    return {Fraction(1, 4): "=", Fraction(1, 2): "_", Fraction(3, 4): "_.", Fraction(1): "", Fraction(3, 2): "."}.get(duration)


def note_parts(duration: Fraction) -> list[Fraction]:
    if suffix(duration) is not None:
        return [duration]
    parts: list[Fraction] = []
    remaining = duration
    for value in (Fraction(3, 2), Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(1, 4)):
        while remaining >= value:
            parts.append(value)
            remaining -= value
    if remaining:
        raise ValueError(f"cannot encode Jianpu duration {duration}")
    return parts


def rest_parts(duration: Fraction) -> list[Fraction]:
    parts: list[Fraction] = []
    remaining = duration
    for value in (Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(1, 4)):
        while remaining >= value:
            parts.append(value)
            remaining -= value
    if remaining:
        raise ValueError(f"cannot encode Jianpu rest duration {duration}")
    return parts


def events_to_notation(events: list[tuple[Fraction, Fraction, int]], *, title: str, bpm: int = 90, meter_bpb: int = 4) -> str:
    """Encode the normalized event timeline as real HC Jianpu notation."""
    del title
    header = [f"{{bpm:{bpm}}}", "{key_signature:C}", "{time_signature:4/4}"]
    body: list[str] = []
    cursor = Fraction(0)
    bar = Fraction(meter_bpb)

    def add_bar_if_ready() -> None:
        if cursor > 0 and cursor % bar == 0 and (not body or body[-1] != "|"):
            body.append("|")

    def emit_rests(duration: Fraction) -> None:
        nonlocal cursor
        for part in rest_parts(duration):
            body.append("0" + (suffix(part) or ""))
            cursor += part
            add_bar_if_ready()

    def emit_note(onset: Fraction, duration: Fraction, midi: int) -> None:
        nonlocal cursor
        head = pitch_head(midi)
        pieces: list[Fraction] = []
        starts: list[Fraction] = []
        remaining = duration
        piece_cursor = onset
        while remaining:
            to_bar = bar - (piece_cursor % bar)
            piece_duration = min(remaining, to_bar)
            for part in note_parts(piece_duration):
                pieces.append(part)
                starts.append(piece_cursor)
                piece_cursor += part
            remaining -= piece_duration
        if len(pieces) > 1:
            body.append("(")
        for index, part in enumerate(pieces):
            part_suffix = suffix(part)
            if part_suffix is not None:
                body.append(head + part_suffix)
            elif part >= 2 and part.denominator == 1:
                body.append(head if index == 0 else "-")
                body.extend("-" for _ in range(int(part) - 2))
            else:
                raise ValueError(f"cannot encode note piece {part}")
            if starts[index] + part == bar * ((starts[index] // bar) + 1):
                body.append("|")
        if len(pieces) > 1:
            body.append(")")
        cursor = onset + duration
        add_bar_if_ready()

    i = 0
    while i < len(events):
        onset, duration, midi = events[i]
        if onset < cursor:
            raise ValueError(f"overlapping event at {onset}, cursor is {cursor}")
        if onset > cursor:
            emit_rests(onset - cursor)
        is_triplet = (
            duration == Fraction(1, 3) and i + 2 < len(events)
            and events[i + 1][0] == onset + Fraction(1, 3)
            and events[i + 2][0] == onset + Fraction(2, 3)
            and events[i + 1][1] == Fraction(1, 3)
            and events[i + 2][1] == Fraction(1, 3)
        )
        if is_triplet:
            body.append("(3:")
            for _, _, triplet_midi in events[i:i + 3]:
                body.append(pitch_head(triplet_midi) + "_")
            body.append(")")
            cursor = onset + 1
            add_bar_if_ready()
            i += 3
            continue
        emit_note(onset, duration, midi)
        i += 1

    return "\n".join(header + [" ".join(body)]) + "\n"


def make_songdata(title: str, notation: str) -> dict:
    return {"songdata": {"song_name": title, "keynote": "1=C", "rhythm": "4/4", "notation": notation}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=os.environ.get("HC_ENGINE", ""))
    args = ap.parse_args()
    if not args.engine:
        ap.error("set HC_ENGINE or pass --engine /path/to/hc_*.js")

    out_dir = ROOT / "examples"
    out_dir.mkdir(exist_ok=True)
    manifest = []
    for slug, title, description, raw_events in CASES:
        events = [(F(o), F(d), midi) for o, d, midi in raw_events]
        kern = jianpu_to_kern(events, meter_bpb=4)
        recovered = kern_to_timeline(kern)
        if events != recovered:
            raise AssertionError(f"{slug}: roundtrip mismatch: {events!r} != {recovered!r}")

        source_json = out_dir / f"{slug}.json"
        source_json.write_text(json.dumps(make_songdata(title, events_to_notation(events, title=title)), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out_dir / f"{slug}.krn").write_text(kern + "\n", encoding="utf-8")
        before = out_dir / f"{slug}_before.svg"
        after = out_dir / f"{slug}_after.svg"
        comparison = out_dir / f"{slug}_comparison.svg"
        render_json(source_json, before, engine=args.engine, title=title)
        after_payload = make_songdata(f"{title} | Kern roundtrip", events_to_notation(recovered, title=title))
        with tempfile.TemporaryDirectory(prefix=f"{slug}-after-") as tmp:
            after_json = Path(tmp) / f"{slug}_after.json"
            after_json.write_text(json.dumps(after_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            render_json(after_json, after, engine=args.engine, title=after_payload["songdata"]["song_name"])
        render_comparison_svg(before, after, comparison, title=title)
        manifest.append({"slug": slug, "title": title, "description": description, "events": len(events), "exact": True, "renderer": "hc.parse"})

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(manifest)} HC-rendered examples in {out_dir}")


if __name__ == "__main__":
    main()
