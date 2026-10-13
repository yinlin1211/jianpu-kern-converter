"""MiniUnSongData/phrase.json <-> **kern adaptation.

The adapter has three deliberately separate layers:

* ``MiniNote`` reads the source ``notes`` representation.
* ``TimelineEvent`` is the exact semantic representation used by the existing
  ``jianpu_to_kern`` and ``kern_to_timeline`` functions.
* ``lyric_sidecar`` carries information that **kern cannot encode.

Durations are kept as :class:`fractions.Fraction` until JSON serialization.
This module does not claim that a regenerated notation string is byte-for-byte
identical to the source notation.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .from_kern import dur_of, kern_to_timeline
from .pitch import kern_to_midi, midi_to_kern
from .to_kern import SINGLE, split_by_barline, split_by_duration


MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
NOTE_PITCH_CLASSES = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}
METER_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
KERN_METER_RE = re.compile(r"^\*M(\d+)/(\d+)")


class MiniUnSongDataError(ValueError):
    """Base error for malformed or unsupported MiniUnSongData."""


class MiniRepresentabilityError(MiniUnSongDataError):
    """Raised when a precise Kern event cannot be represented by Mini fields."""


@dataclass(frozen=True)
class Meter:
    """A single time signature and its length in quarter-note beats."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("meter numerator and denominator must be positive")

    @property
    def bar_beats(self) -> Fraction:
        return Fraction(self.numerator * 4, self.denominator)

    @property
    def text(self) -> str:
        return f"{self.numerator}/{self.denominator}"

    @classmethod
    def parse(cls, value: "Meter | str | int | None") -> "Meter":
        if isinstance(value, cls):
            return value
        if value is None or str(value).strip() == "":
            return cls(4, 4)
        text = str(value).strip()
        # Mixed-meter strings occur in full song metadata.  A single-spine
        # adapter must not silently treat them as one fixed meter.
        if len(text.split()) != 1:
            raise MiniUnSongDataError(
                f"mixed meter {value!r} is not supported by one fixed timeline"
            )
        match = METER_RE.match(text)
        if match:
            return cls(int(match.group(1)), int(match.group(2)))
        if text.isdigit():
            return cls(int(text), 4)
        raise MiniUnSongDataError(f"invalid meter: {value!r}")


def parse_meter(value: Meter | str | int | None) -> Meter:
    """Public shorthand for :meth:`Meter.parse`."""

    return Meter.parse(value)


def _as_fraction(value: Any) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        # Decimal text avoids importing the binary approximation of a float.
        return Fraction(str(value))
    return Fraction(value)


def fraction_text(value: Fraction | int | float) -> str:
    """Serialize an exact beat value as ``N`` or ``N/D``."""

    value = _as_fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


@dataclass(frozen=True)
class MiniNote:
    """One raw ``phrase.json`` note entry.

    ``raw`` is retained so unknown metadata such as ``source_id`` survives a
    caller's own serialization.  The adapter only gives musical meaning to
    the fields documented here.
    """

    index: int
    content: str
    under_bar_count: int
    dot_count: int
    octave: int
    lyric: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any], index: int) -> "MiniNote":
        if not isinstance(raw, Mapping):
            raise MiniUnSongDataError(f"notes[{index}] must be an object")
        rest = _truthy(raw.get("rest"))
        content_value = raw.get("content")
        if content_value is None and rest:
            content_value = "0"
        if content_value is None:
            raise MiniUnSongDataError(f"notes[{index}] has no content")
        try:
            under = int(raw.get("underBarCount", 0) or 0)
            dots = int(raw.get("dotCount", 0) or 0)
            octave = int(raw.get("octave", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise MiniUnSongDataError(
                f"notes[{index}] has non-integer underBarCount/dotCount/octave"
            ) from exc
        if under < 0 or dots < 0:
            raise MiniUnSongDataError(f"notes[{index}] has a negative duration marker")
        lyric = raw.get("lyric")
        return cls(
            index=index,
            content=str(content_value).strip(),
            under_bar_count=under,
            dot_count=dots,
            octave=octave,
            lyric=None if lyric is None else str(lyric),
            raw=dict(raw),
        )

    @property
    def normalized_content(self) -> str:
        return self.content.lower()

    @property
    def is_yan(self) -> bool:
        return self.normalized_content in {"yan", "-", "sustain", "tie"}

    @property
    def is_skip(self) -> bool:
        return self.normalized_content in {"jie", "skip"}

    @property
    def is_rest(self) -> bool:
        return (
            _truthy(self.raw.get("rest"))
            or self.normalized_content in {"0", "rest", "r"}
        )

    @property
    def degree(self) -> int:
        if self.is_rest or self.is_yan or self.is_skip:
            raise MiniUnSongDataError(
                f"notes[{self.index}] content {self.content!r} is not a scale degree"
            )
        if self.content not in {str(i) for i in range(1, 8)}:
            raise MiniUnSongDataError(
                f"notes[{self.index}] content {self.content!r} is not a Jianpu degree"
            )
        return int(self.content)

    @property
    def written_duration(self) -> Fraction:
        """The duration encoded by underbars and dots.

        ``yan`` is handled separately because it extends the preceding event
        by one beat in MiniUnSongData; its own markers are not a new event.
        """

        if self.is_yan:
            return Fraction(1)
        base = Fraction(1, 2**self.under_bar_count)
        dotted_factor = 2 - Fraction(1, 2**self.dot_count)
        duration = base * dotted_factor
        if duration <= 0:
            raise MiniUnSongDataError(f"notes[{self.index}] has a non-positive duration")
        return duration

    @property
    def tie_group(self) -> str | None:
        value = self.raw.get("tie_group")
        return None if value is None else str(value)


@dataclass
class TimelineEvent:
    """Exact monophonic semantic event on an absolute quarter-beat axis."""

    kind: str
    onset: Fraction
    duration: Fraction
    midi: int | None
    lyric: str | None = None
    source_indices: tuple[int, ...] = ()
    measure: int | None = None
    tie_group: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"note", "rest"}:
            raise ValueError(f"invalid event kind: {self.kind!r}")
        if self.duration <= 0:
            raise ValueError("event duration must be positive")
        if self.kind == "note" and self.midi is None:
            raise ValueError("note event requires midi")
        if self.kind == "rest" and self.midi is not None:
            raise ValueError("rest event cannot have midi")

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration


@dataclass(frozen=True)
class LyricEntry:
    """A lyric attached to an event index outside the Kern text."""

    event_index: int
    lyric: str

    def as_dict(self) -> dict[str, Any]:
        return {"event_index": self.event_index, "lyric": self.lyric}


@dataclass
class MiniScore:
    """Parsed MiniUnSongData payload."""

    songdata: dict[str, Any]
    notes: tuple[MiniNote, ...]
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "MiniScore":
        if not isinstance(payload, Mapping):
            raise MiniUnSongDataError("MiniUnSongData payload must be an object")
        songdata = payload.get("songdata")
        if not isinstance(songdata, Mapping):
            raise MiniUnSongDataError("payload must contain an object named songdata")
        raw_notes = payload.get("notes")
        if not isinstance(raw_notes, list):
            raise MiniUnSongDataError("payload must contain a list named notes")
        notes = tuple(MiniNote.from_raw(raw, i) for i, raw in enumerate(raw_notes))
        extra = {key: copy.deepcopy(value) for key, value in payload.items() if key != "notes"}
        extra["songdata"] = copy.deepcopy(dict(songdata))
        return cls(songdata=copy.deepcopy(dict(songdata)), notes=notes, extra=extra)

    @classmethod
    def load(cls, path: str | Path) -> "MiniScore":
        return cls.from_payload(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def meter(self) -> Meter:
        return Meter.parse(self.songdata.get("rhythm"))

    @property
    def keynote(self) -> str:
        return str(self.songdata.get("keynote") or "1=C")

    def payload_without_notes(self) -> dict[str, Any]:
        return copy.deepcopy(self.extra)

    def payload(self) -> dict[str, Any]:
        result = self.payload_without_notes()
        result["notes"] = [copy.deepcopy(dict(note.raw)) for note in self.notes]
        return result


def load_mini_unisongdata(path: str | Path) -> MiniScore:
    """Load a MiniUnSongData/phrase.json file."""

    return MiniScore.load(path)


def _with_notation_structure(score: MiniScore, runtime_dir: str | Path | None = None) -> MiniScore:
    if score.extra.get("notation_structure") or not score.songdata.get("notation"):
        return score
    # Reverse-generated notes already carry the event identities from Kern.
    if score.extra.get("conversion", {}).get("adapter") == "jianpu_kern.mini_unisongdata":
        return score
    local_runtime = Path(__file__).resolve().parents[2] / "jp-svt/B_tools/jianpu_dataset/scripts/website_renderer/runtime"
    configured = runtime_dir or os.environ.get("MINIUNISONGDATA_RUNTIME")
    selected = Path(configured) if configured else local_runtime
    if not selected.is_dir():
        if "(" in str(score.songdata["notation"]) or ")" in str(score.songdata["notation"]):
            raise MiniRepresentabilityError(
                "Notation connections require the official runtime; supply runtime_dir "
                "or MINIUNISONGDATA_RUNTIME instead of dropping the connections."
            )
        return score
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("notation_structure.js")), str(selected)],
        input=json.dumps(score.payload(), ensure_ascii=False),
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise MiniUnSongDataError(f"Official notation parser: {result.stderr.strip()}")
    structure = json.loads(result.stdout)
    if len(structure["notes"]) != len(score.notes):
        raise MiniRepresentabilityError("Official notation notes do not match the stored phrase notes.")
    payload = score.payload()
    for raw, parsed in zip(payload["notes"], structure["notes"]):
        if str(raw["content"]) != str(parsed["content"]) or int(raw.get("octave") or 0) != int(parsed["octave"]):
            raise MiniRepresentabilityError("Official notation and stored phrase pitches differ.")
        raw["playback_onset"] = str(Fraction(str(parsed["onset"])).limit_denominator(1536))
        raw["playback_duration"] = str(Fraction(str(parsed["duration"])).limit_denominator(1536))
        raw["playback_head"] = str(Fraction(str(parsed["head"])).limit_denominator(1536))
        raw["playback_midi"] = parsed["midi"]
        raw["lyric"] = parsed["lyric"] or None
    payload["notation_structure"] = {
        "connections": structure["connections"],
        "total_beats": str(Fraction(str(structure["total_beats"])).limit_denominator(1536)),
        # Beat positions of the bar cells actually written in the notation.
        "barlines": [
            str(Fraction(str(beat)).limit_denominator(1536))
            for beat in structure.get("barlines", [])
        ],
    }
    return MiniScore.from_payload(payload)


def _key_pitch_class(keynote: str | None) -> int:
    text = str(keynote or "1=C").strip()
    token = text.split("=")[-1].strip().replace("♯", "#").replace("♭", "b")
    token = token.replace("调", "")
    token = re.sub(r"\d+$", "", token)
    if token.startswith("#"):
        token = token[1:] + "#"
    if token.startswith("b"):
        token = token[1:] + "b"
    token = token[0].upper() + token[1:] if token else "C"
    if token not in NOTE_PITCH_CLASSES:
        raise MiniUnSongDataError(f"unsupported keynote: {keynote!r}")
    return NOTE_PITCH_CLASSES[token]


def keynote_to_midi(keynote: str | None) -> int:
    """Return the MIDI pitch of Jianpu degree 1 at octave 0."""

    return 60 + _key_pitch_class(keynote)


def degree_to_midi(degree: int, octave: int, keynote: str | None = "1=C") -> int:
    if degree not in range(1, 8):
        raise MiniUnSongDataError(f"degree must be 1..7, got {degree!r}")
    return keynote_to_midi(keynote) + MAJOR_SCALE[degree - 1] + 12 * int(octave)


def midi_to_degree(midi: int, keynote: str | None = "1=C") -> tuple[int, int]:
    """Map a diatonic MIDI pitch to ``(degree, octave)``.

    MiniUnSongData's note schema has no independent accidental field.  A
    chromatic Kern pitch therefore raises instead of being silently altered.
    """

    relative = int(midi) - keynote_to_midi(keynote)
    pitch_class = relative % 12
    if pitch_class not in MAJOR_SCALE:
        raise MiniRepresentabilityError(
            f"MIDI {midi} is chromatic relative to {keynote or '1=C'}; "
            "MiniUnSongData notes have no accidental field"
        )
    degree = MAJOR_SCALE.index(pitch_class) + 1
    octave = (relative - pitch_class) // 12
    return degree, octave


def mini_to_timeline(
    payload: MiniScore | Mapping[str, Any],
    *,
    keynote: str | None = None,
    runtime_dir: str | Path | None = None,
) -> list[TimelineEvent]:
    """Parse ``songdata + notes`` into exact semantic events.

    ``yan`` is not emitted as an event: it adds one quarter-note beat to the
    previous event.  ``jie``/``skip`` is metadata-only and consumes no time.
    Explicit ``rest`` fields and ``content:"0"`` both produce rest events.
    """

    score = payload if isinstance(payload, MiniScore) else MiniScore.from_payload(payload)
    score = _with_notation_structure(score, runtime_dir)
    tonic = keynote if keynote is not None else score.keynote
    cursor = Fraction(0)
    events: list[TimelineEvent] = []

    for note in score.notes:
        if "playback_onset" in note.raw:
            onset = _as_fraction(note.raw["playback_onset"])
            duration = _as_fraction(note.raw["playback_duration"])
            if duration == 0:
                if not events or onset >= events[-1].end:
                    raise MiniRepresentabilityError("Unclaimed zero-duration notation continuation.")
                events[-1].source_indices += (note.index,)
                continue
            events.append(TimelineEvent(
                kind="rest" if note.is_rest else "note",
                onset=onset, duration=duration,
                midi=None if note.is_rest else (
                    int(note.raw["playback_midi"]) if keynote is None
                    else degree_to_midi(note.degree, note.octave, tonic)
                ),
                lyric=None if note.is_rest else note.lyric,
                source_indices=(note.index,),
                measure=note.raw.get("measure"),
            ))
            continue
        if note.is_skip:
            continue
        if note.is_yan:
            if not events:
                raise MiniUnSongDataError(
                    f"notes[{note.index}] is yan but has no preceding event"
                )
            previous = events[-1]
            previous.duration += Fraction(1)
            previous.source_indices += (note.index,)
            cursor += Fraction(1)
            continue

        duration = note.written_duration
        kind = "rest" if note.is_rest else "note"
        midi = None if kind == "rest" else degree_to_midi(note.degree, note.octave, tonic)
        measure_value = note.raw.get("measure")
        try:
            measure = None if measure_value is None else int(measure_value)
        except (TypeError, ValueError):
            measure = None

        # Reverse-generated payloads use tie_group to carry the event identity
        # across a necessary Mini note split.  Original phrase files normally
        # have no such field, so ordinary adjacent notes stay independent.
        can_merge = bool(note.tie_group) or _truthy(note.raw.get("isTiedNote"))
        if (
            can_merge
            and events
            and events[-1].kind == kind
            and events[-1].midi == midi
            and events[-1].end == cursor
            and (
                note.tie_group is None
                or events[-1].tie_group == note.tie_group
            )
        ):
            previous = events[-1]
            previous.duration += duration
            previous.source_indices += (note.index,)
            if previous.lyric is None and note.lyric is not None:
                previous.lyric = note.lyric
        else:
            events.append(
                TimelineEvent(
                    kind=kind,
                    onset=cursor,
                    duration=duration,
                    midi=midi,
                    lyric=note.lyric if kind == "note" else None,
                    source_indices=(note.index,),
                    measure=measure,
                    tie_group=note.tie_group,
                )
            )
        cursor += duration

    return events


def _note_rows(events: Iterable[TimelineEvent]) -> list[tuple[Fraction, Fraction, int]]:
    rows: list[tuple[Fraction, Fraction, int]] = []
    previous_end = Fraction(0)
    for event in events:
        if event.kind != "note":
            continue
        if event.onset < previous_end:
            raise MiniUnSongDataError("overlapping note events cannot be written to one Kern spine")
        rows.append((event.onset, event.duration, int(event.midi)))
        previous_end = event.end
    if not rows:
        raise MiniUnSongDataError("at least one note event is required for Kern conversion")
    return rows


def _replace_kern_meter(kern: str, meter: Meter) -> str:
    """Keep the source denominator in the Kern header.

    The existing event converter uses quarter-note beats internally and emits
    ``*M{bar_beats}/4``.  Its timeline is correct for e.g. 6/8, but the header
    should still say 6/8 for a downstream renderer.
    """

    lines = kern.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("*M"):
            lines[index] = f"*M{meter.text}"
            return "\n".join(lines)
    if lines:
        lines.insert(1, f"*M{meter.text}")
    return "\n".join(lines)


@dataclass
class MiniToKernResult:
    """Result of MiniUnSongData -> Kern conversion."""

    kern: str
    events: list[TimelineEvent]
    meter: Meter
    lyric_sidecar: list[LyricEntry]
    source_metadata: dict[str, Any]

    def sidecar(self) -> dict[str, Any]:
        return {
            "format": "jianpu-kern-mini-sidecar-v1",
            "meter": self.meter.text,
            "lyrics_in_kern": False,
            "lyric_policy": "sidecar",
            "lyric_sidecar": [entry.as_dict() for entry in self.lyric_sidecar],
            "events": [
                {
                    "event_index": index,
                    "kind": event.kind,
                    "onset": fraction_text(event.onset),
                    "duration": fraction_text(event.duration),
                    "midi": event.midi,
                    "lyric": event.lyric,
                    "source_indices": list(event.source_indices),
                }
                for index, event in enumerate(self.events)
            ],
            "source_metadata": copy.deepcopy(self.source_metadata),
        }


def mini_to_kern(
    payload: MiniScore | Mapping[str, Any],
    *,
    meter: Meter | str | int | None = None,
    keynote: str | None = None,
    key_sig: str | None = None,
    runtime_dir: str | Path | None = None,
) -> MiniToKernResult:
    """Convert a MiniUnSongData payload to a single-spine Kern string."""

    score = payload if isinstance(payload, MiniScore) else MiniScore.from_payload(payload)
    score = _with_notation_structure(score, runtime_dir)
    selected_meter = Meter.parse(meter if meter is not None else score.songdata.get("rhythm"))
    events = mini_to_timeline(score, keynote=keynote)
    _note_rows(events)
    kern = _connected_kern(score, events, selected_meter, key_sig)
    lyrics = [
        LyricEntry(index, event.lyric)
        for index, event in enumerate(events)
        if event.kind == "note" and event.lyric is not None
    ]
    return MiniToKernResult(
        kern=kern,
        events=events,
        meter=selected_meter,
        lyric_sidecar=lyrics,
        source_metadata=score.payload_without_notes(),
    )


def _source_barlines(score: MiniScore, meter: Meter) -> list[Fraction]:
    """Prefer parsed syntax, then recovered boundaries, then stored measures.

    None of these change the time axis: a short/long measure stays short/long.
    Only sources without any boundary information use the fixed-meter grid.
    """
    structure = score.extra.get("notation_structure", {})
    conversion = score.extra.get("conversion", {})
    for metadata in (structure, conversion):
        if "barlines" in metadata:
            return sorted({_as_fraction(beat) for beat in metadata["barlines"] if _as_fraction(beat) > 0})
    cursor = Fraction(0)
    previous_measure = None
    boundaries = []
    have_measures = False
    for note in score.notes:
        if note.is_skip:
            continue
        measure = note.raw.get("measure")
        if measure is not None:
            have_measures = True
            if previous_measure is not None and measure != previous_measure and cursor > 0:
                boundaries.append(cursor)
            previous_measure = measure
        cursor += _as_fraction(note.raw.get("playback_head", note.written_duration))
    if have_measures:
        # Stored measure numbers cannot say whether the fragment is closed.
        return sorted({*boundaries, cursor})
    grid = {meter.bar_beats * i for i in range(1, int(cursor // meter.bar_beats) + 1)}
    return sorted(grid | {cursor})


def _split_at_boundaries(
    onset: Fraction, duration: Fraction, boundaries: Sequence[Fraction],
) -> list[tuple[Fraction, Fraction]]:
    end = onset + duration
    points = [onset, *(beat for beat in boundaries if onset < beat < end), end]
    return [(start, stop - start) for start, stop in zip(points, points[1:])]


def _connected_kern(score: MiniScore, events: list[TimelineEvent], meter: Meter, key_sig: str | None) -> str:
    barlines = _source_barlines(score, meter)
    event_by_note = {index: ei for ei, event in enumerate(events) for index in event.source_indices}
    blocks = []
    cursor = Fraction(0)
    for note in score.notes:
        if note.is_skip:
            continue
        ei = event_by_note[note.index]
        event = events[ei]
        onset = _as_fraction(note.raw.get("playback_onset", cursor))
        duration = _as_fraction(note.raw.get("playback_head", note.written_duration))
        for start, span in _split_at_boundaries(onset, duration, barlines):
            for part in split_by_duration(span):
                blocks.append({
                    "onset": start, "duration": part, "event": ei,
                    "midi": event.midi, "source": note.index,
                    "content": "yan" if note.is_yan else "rest" if note.is_rest else "note",
                })
                start += part
        cursor = onset + duration
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    for connection in score.extra.get("notation_structure", {}).get("connections", []):
        members = [i for i, block in enumerate(blocks) if connection["start"] <= block["source"] <= connection["end"] and block["midi"] is not None]
        if len(members) < 2 or len({blocks[i]["event"] for i in members}) == 1:
            continue
        starts[members[0]] = starts.get(members[0], 0) + 1
        ends[members[-1]] = ends.get(members[-1], 0) + 1
    event_members: dict[int, list[int]] = {}
    for index, block in enumerate(blocks):
        event_members.setdefault(block["event"], []).append(index)
    lines = ["**kern"]
    if key_sig:
        lines.append(f"*k[{key_sig}]")
    lines.append(f"*M{meter.text}")
    # Humdrum writes a pickup without a leading barline; the first "=" then
    # falls on the source's first real measure boundary.
    interior = {beat for beat in barlines if 0 < beat < cursor}
    if not barlines or barlines[0] >= meter.bar_beats:
        lines.append("=")
    for index, block in enumerate(blocks):
        if index and block["onset"] in interior:
            lines.append("=")
        if not index or block["event"] != blocks[index - 1]["event"]:
            lines.append(f"!mini-event:{block['event']}")
        # Kern uses pitched tie continuations for both a second numeric head
        # and a Jianpu sustain dash. Preserve that distinction for lyric slots.
        lines.append(f"!mini-content:{block['content']}")
        midi = block["midi"]
        token = SINGLE[block["duration"]] + ("r" if midi is None else midi_to_kern(midi))
        chain = event_members[block["event"]]
        if midi is not None and len(chain) > 1:
            token += "[" if index == chain[0] else "]" if index == chain[-1] else "_"
        token += "(" * starts.get(index, 0) + ")" * ends.get(index, 0)
        lines.append(token)
    # The final barline is written only when the source closes the fragment.
    return "\n".join([*lines, *(["=="] if cursor in barlines else []), "*-"])


def _meter_from_kern(kern: str) -> Meter | None:
    for line in kern.splitlines():
        match = KERN_METER_RE.match(line.strip())
        if match:
            return Meter(int(match.group(1)), int(match.group(2)))
    return None


def _normalize_lyric_sidecar(
    value: Mapping[Any, Any] | Sequence[Mapping[str, Any] | LyricEntry] | None,
) -> dict[int, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping) and "lyric_sidecar" in value:
        value = value["lyric_sidecar"]
    entries: Iterable[Any]
    if isinstance(value, Mapping):
        entries = ({"event_index": key, "lyric": lyric} for key, lyric in value.items())
    else:
        entries = value
    result: dict[int, str] = {}
    for entry in entries:
        if isinstance(entry, LyricEntry):
            index, lyric = entry.event_index, entry.lyric
        elif isinstance(entry, Mapping):
            index, lyric = entry.get("event_index"), entry.get("lyric")
        else:
            raise MiniUnSongDataError("lyric sidecar entries must be objects")
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise MiniUnSongDataError(f"invalid lyric event index: {index!r}") from exc
        if lyric is not None:
            result[index] = str(lyric)
    return result


def _split_at_bars(
    onset: Fraction, duration: Fraction, bar: Fraction
) -> list[tuple[Fraction, Fraction]]:
    pieces: list[tuple[Fraction, Fraction]] = []
    cursor = onset
    end = onset + duration
    while cursor < end:
        next_bar = (cursor // bar + 1) * bar
        piece_end = min(end, next_bar)
        pieces.append((cursor, piece_end - cursor))
        cursor = piece_end
    return pieces


def _mini_atoms() -> list[tuple[Fraction, int, int]]:
    atoms = []
    # The normal phrase corpus uses <= 3 underbars and <= 2 dots.  A larger
    # underbar range is useful for exact Kern values while remaining valid
    # notation for the original renderer.
    for under in range(0, 9):
        for dots in range(0, 3):
            value = Fraction(1, 2**under) * (2 - Fraction(1, 2**dots))
            atoms.append((value, under, dots))
    return sorted(atoms, key=lambda item: (item[0], -item[1]), reverse=True)


def _decompose_mini_duration(duration: Fraction) -> list[tuple[Fraction, int, int]]:
    """Split a dyadic duration into exact Mini underbar/dot atoms."""

    if duration <= 0:
        raise MiniRepresentabilityError(f"non-positive Mini duration: {duration}")
    remaining = duration
    atoms = _mini_atoms()
    result: list[tuple[Fraction, int, int]] = []
    while remaining:
        chosen = next((atom for atom in atoms if atom[0] <= remaining), None)
        if chosen is None:
            raise MiniRepresentabilityError(
                f"MiniUnSongData cannot encode exact duration {duration}; "
                "use an exact Fraction sidecar or a richer tuplet field"
            )
        result.append(chosen)
        remaining -= chosen[0]
    return result


def _suffix(under_bar_count: int, dot_count: int) -> str:
    base = "=" * (under_bar_count // 2) + "_" * (under_bar_count % 2)
    return base + "." * dot_count


def _notation_pitch(degree: int, octave: int) -> str:
    return str(degree) + ("g" * octave if octave > 0 else "," * -octave)


@dataclass(frozen=True)
class _GeneratedToken:
    kind: str
    duration: Fraction
    under_bar_count: int
    dot_count: int
    midi: int | None
    degree: int | None
    octave: int
    event_index: int | None
    group: str
    lyric: str | None = None
    tied: bool = False

    def notation(self) -> str:
        if self.kind == "yan":
            return "-"
        suffix = _suffix(self.under_bar_count, self.dot_count)
        if self.kind == "rest":
            return "0" + suffix
        return _notation_pitch(int(self.degree), self.octave) + suffix

    def as_note(self, measure: int) -> dict[str, Any]:
        content = "0" if self.kind == "rest" else "yan" if self.kind == "yan" else str(self.degree)
        return {
            "source_id": None,
            "occurrence": 0,
            "measure": measure,
            "content": content,
            "scale": 0 if self.kind in {"rest", "yan"} else int(self.degree),
            "octave": 0 if self.kind == "rest" else self.octave,
            "underBarCount": self.under_bar_count,
            "dotCount": self.dot_count,
            "isTiedNote": self.tied,
            "lyric": self.lyric,
            "tie_group": self.group,
            "event_index": self.event_index,
        }


def _template_parts(
    template: MiniScore | Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if template is None:
        return {}, {}
    if isinstance(template, MiniScore):
        return template.payload_without_notes(), copy.deepcopy(template.songdata)
    if not isinstance(template, Mapping):
        raise MiniUnSongDataError("template must be a MiniScore or JSON object")
    raw = copy.deepcopy(dict(template))
    songdata = raw.get("songdata")
    if not isinstance(songdata, Mapping):
        raise MiniUnSongDataError("template must contain songdata")
    raw.pop("notes", None)
    return raw, copy.deepcopy(dict(songdata))


@dataclass
class KernToMiniResult:
    """Result of Kern -> renderable MiniUnSongData conversion."""

    payload: dict[str, Any]
    events: list[TimelineEvent]
    meter: Meter
    lyric_sidecar: list[LyricEntry]

    def sidecar(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload.get("conversion", {}))


def _read_kern_blocks(kern: str) -> list[dict[str, Any]]:
    blocks = []
    cursor = Fraction(0)
    event_index = -1
    explicit_event = None
    content = None
    tied = False
    cell_re = re.compile(r"^(\d+\.*)([a-gA-Gr]+)([#n-]*)([LJ\[\]_()]*)$")
    for line in kern.splitlines():
        line = line.strip()
        if line.startswith("!mini-event:"):
            explicit_event = int(line.split(":", 1)[1])
            continue
        if line.startswith("!mini-content:"):
            content = line.split(":", 1)[1]
            if content not in {"note", "rest", "yan"}:
                raise MiniRepresentabilityError(f"Invalid Mini content annotation: {content!r}")
            continue
        if not line or line.startswith(("!", "*", "=")):
            continue
        token = line.split("\t")[0]
        match = cell_re.fullmatch(token)
        if not match:
            raise MiniRepresentabilityError(f"Unsupported Kern connection token: {token!r}")
        duration = dur_of(match[1])
        rest = match[2] == "r"
        flags = match[4]
        if explicit_event is not None:
            event_index = explicit_event
        elif not tied:
            event_index += 1
        blocks.append({
            "onset": cursor, "duration": duration, "event": event_index,
            "midi": None if rest else kern_to_midi(token),
            "slur_start": flags.count("("), "slur_end": flags.count(")"),
            "content": content,
        })
        content = None
        if "]" in flags:
            tied = False
        elif "[" in flags:
            tied = True
        cursor += duration
    if tied:
        raise MiniRepresentabilityError("Unclosed Kern tie.")
    return blocks


def _kern_barlines(kern: str) -> tuple[list[Fraction], bool]:
    """Return barline beats after the first data token and final-bar presence."""

    cursor = Fraction(0)
    beats: list[Fraction] = []
    for line in kern.splitlines():
        token = line.strip().split("\t")[0]
        if token.startswith("="):
            if cursor > 0 and (not beats or beats[-1] != cursor):
                beats.append(cursor)
            continue
        if not token or token.startswith(("!", "*")):
            continue
        match = re.match(r"^(\d+\.*)", token)
        if match:
            cursor += dur_of(match[1])
    has_final = bool(beats) and beats[-1] == cursor
    return [beat for beat in beats if beat < cursor], has_final


def kern_to_mini(
    kern: str,
    *,
    template: MiniScore | Mapping[str, Any] | None = None,
    meter: Meter | str | int | None = None,
    keynote: str | None = None,
    lyric_sidecar: Mapping[Any, Any] | Sequence[Mapping[str, Any] | LyricEntry] | None = None,
) -> KernToMiniResult:
    """Create a renderable Mini payload from a single-spine Kern string.

    Kern notes are mapped to Mini note fields and gaps become explicit rests.
    Lyrics are never read from Kern.  Pass a sidecar explicitly when lyrics
    should be attached to the generated ``notes`` entries.
    """

    template_payload, template_songdata = _template_parts(template)
    selected_meter = Meter.parse(
        meter
        if meter is not None
        else template_songdata.get("rhythm") or _meter_from_kern(kern) or Meter(4, 4)
    )
    selected_keynote = keynote or str(template_songdata.get("keynote") or "1=C")
    sidecar_map = _normalize_lyric_sidecar(lyric_sidecar)
    blocks = _read_kern_blocks(kern)
    total = sum((block["duration"] for block in blocks), Fraction(0))
    barlines, has_final_bar = _kern_barlines(kern)
    if not barlines and total > selected_meter.bar_beats:
        # Kern without interior barlines: only then fall back to the meter grid.
        barlines = [
            selected_meter.bar_beats * i
            for i in range(1, int(total // selected_meter.bar_beats) + 1)
            if selected_meter.bar_beats * i < total
        ]
        has_final_bar = total % selected_meter.bar_beats == 0
    events: list[TimelineEvent] = []
    event_ids = []
    for block in blocks:
        kind = "rest" if block["midi"] is None else "note"
        if event_ids and event_ids[-1] == block["event"]:
            if events[-1].midi != block["midi"]:
                raise MiniRepresentabilityError("A Kern tie connects different pitches.")
            events[-1].duration += block["duration"]
        else:
            event_ids.append(block["event"])
            events.append(TimelineEvent(kind, block["onset"], block["duration"], block["midi"]))
    if not any(event.kind == "note" for event in events):
        raise MiniUnSongDataError("Kern contains no note events")

    tokens: list[_GeneratedToken] = []
    prefixes: dict[int, int] = {}
    suffixes: dict[int, int] = {}
    group_members: dict[int, list[int]] = {}
    for block in blocks:
        event_index = block["event"]
        rest = block["midi"] is None
        first_token = len(tokens)
        members = group_members.setdefault(event_index, [])
        encoded = _encode_range(
            "rest" if rest else "note", block["onset"], block["duration"],
            event_index, None if rest else midi_to_degree(block["midi"], selected_keynote),
            barlines,
            sidecar_map.get(event_index) if not members and not rest else None,
        )
        for token in encoded:
            # HC handles one pair of pitched tie heads plus integer sustain
            # dashes. A flat group of three repeated pitch heads retriggers.
            is_sustain = block["content"] == "yan"
            implicit_sustain = block["content"] is None and not rest and len(members) >= 2
            if is_sustain or implicit_sustain:
                if token.duration != 1:
                    raise MiniRepresentabilityError(
                        "Fractional sustain dashes require a richer Mini tie spelling."
                    )
                token = replace(token, kind="yan", degree=None, under_bar_count=0, dot_count=0)
            members.append(len(tokens))
            tokens.append(token)
        if block["slur_start"]:
            prefixes[first_token] = prefixes.get(first_token, 0) + block["slur_start"]
        if block["slur_end"]:
            suffixes[len(tokens) - 1] = suffixes.get(len(tokens) - 1, 0) + block["slur_end"]
    for event_index, members in group_members.items():
        if tokens[members[0]].kind == "rest" or len(members) < 2:
            continue
        prefixes[members[0]] = prefixes.get(members[0], 0) + 1
        suffixes[members[-1]] = suffixes.get(members[-1], 0) + 1
        for index in members[1:]:
            tokens[index] = replace(tokens[index], tied=True)

    notes: list[dict[str, Any]] = []
    notation_tokens: list[str] = []
    notation_cursor = Fraction(0)
    bar_after = set(barlines) | ({total} if has_final_bar else set())
    for index, token in enumerate(tokens):
        measure = sum(beat <= notation_cursor for beat in barlines) + 1
        notes.append(token.as_note(measure))
        notation_tokens.append(
            "(" * prefixes.get(index, 0) + token.notation() + ")" * suffixes.get(index, 0)
        )
        notation_cursor += token.duration
        if notation_cursor in bar_after:
            notation_tokens.append("|")

    songdata = copy.deepcopy(template_songdata)
    songdata["rhythm"] = selected_meter.text
    songdata.setdefault("keynote", selected_keynote)
    songdata["notation"] = (
        f"{{key_signature:{selected_keynote.split('=')[-1]}}}\n"
        f"{{time_signature:{selected_meter.text}}}\n"
        + " ".join(notation_tokens)
        + "\n"
    )

    lyric_entries = [
        LyricEntry(index, lyric)
        for index, lyric in sorted(sidecar_map.items())
        if 0 <= index < len(events)
    ]
    conversion: dict[str, Any] = {
        "adapter": "jianpu_kern.mini_unisongdata",
        "version": 1,
        "meter": selected_meter.text,
        "lyrics_in_kern": False,
        "lyric_policy": "sidecar",
        "lyric_sidecar": [entry.as_dict() for entry in lyric_entries],
        # Includes the closing bar when present, so Mini -> Kern can rewrite it.
        "barlines": [fraction_text(beat) for beat in sorted(bar_after)],
        "events": [
            {
                "event_index": index,
                "kind": event.kind,
                "onset": fraction_text(event.onset),
                "duration": fraction_text(event.duration),
                "midi": event.midi,
            }
            for index, event in enumerate(events)
        ],
    }
    template_payload["songdata"] = songdata
    template_payload["notes"] = notes
    template_payload.pop("notation_structure", None)
    template_payload["conversion"] = conversion
    return KernToMiniResult(
        payload=template_payload,
        events=events,
        meter=selected_meter,
        lyric_sidecar=lyric_entries,
    )


def _encode_range(
    kind: str,
    onset: Fraction,
    duration: Fraction,
    event_index: int | None,
    pitch: tuple[int, int] | None,
    barlines: Sequence[Fraction],
    lyric: str | None,
) -> list[_GeneratedToken]:
    group = f"event-{event_index}" if event_index is not None else ""
    result: list[_GeneratedToken] = []
    first_pitch = pitch
    for piece_onset, piece_duration in _split_at_boundaries(onset, duration, barlines):
        for atom, under, dots in _decompose_mini_duration(piece_duration):
            degree = first_pitch[0] if first_pitch else None
            octave = first_pitch[1] if first_pitch else 0
            result.append(
                _GeneratedToken(
                    kind=kind,
                    duration=atom,
                    under_bar_count=under,
                    dot_count=dots,
                    midi=None,
                    degree=degree,
                    octave=octave,
                    event_index=event_index,
                    group=group,
                    lyric=lyric if not result else None,
                )
            )
        first_pitch = pitch
    if kind == "note" and len(result) > 1:
        result = [replace(token, tied=True) for token in result]
    return result


def kern_to_mini_songdata(
    kern: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convenience wrapper returning only the generated JSON-compatible payload."""

    return kern_to_mini(kern, **kwargs).payload


def json_dump_payload(payload: Mapping[str, Any], path: str | Path) -> None:
    """Write a JSON-compatible Mini payload with stable UTF-8 formatting."""

    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def json_dump_sidecar(result: MiniToKernResult | KernToMiniResult, path: str | Path) -> None:
    """Write the explicit conversion/lyric sidecar for either direction."""

    sidecar = result.sidecar()
    json_dump_payload(sidecar, path)


def main(argv: Sequence[str] | None = None) -> int:
    """Small dependency-free CLI for both directions."""

    import argparse

    parser = argparse.ArgumentParser(description="MiniUnSongData <-> **kern adapter")
    sub = parser.add_subparsers(dest="command", required=True)

    to_kern = sub.add_parser("to-kern", help="convert phrase.json to Kern")
    to_kern.add_argument("input")
    to_kern.add_argument("--out", required=True)
    to_kern.add_argument("--sidecar")
    to_kern.add_argument("--meter")
    to_kern.add_argument("--keynote")
    to_kern.add_argument("--key-sig")
    to_kern.add_argument("--runtime-dir", help="official notation runtime for ties and slurs")

    from_kern = sub.add_parser("from-kern", help="convert Kern to renderable phrase.json")
    from_kern.add_argument("input")
    from_kern.add_argument("--out", required=True)
    from_kern.add_argument("--template")
    from_kern.add_argument("--sidecar")
    from_kern.add_argument("--meter")
    from_kern.add_argument("--keynote")

    args = parser.parse_args(argv)
    if args.command == "to-kern":
        score = MiniScore.load(args.input)
        result = mini_to_kern(
            score,
            meter=args.meter,
            keynote=args.keynote,
            key_sig=args.key_sig,
            runtime_dir=args.runtime_dir,
        )
        Path(args.out).write_text(result.kern + "\n", encoding="utf-8")
        if args.sidecar:
            json_dump_sidecar(result, args.sidecar)
        print(args.out)
        return 0

    template = MiniScore.load(args.template) if args.template else None
    sidecar: Any = None
    if args.sidecar:
        sidecar = json.loads(Path(args.sidecar).read_text(encoding="utf-8"))
        if template is None and isinstance(sidecar, Mapping):
            source_metadata = sidecar.get("source_metadata")
            if isinstance(source_metadata, Mapping) and "songdata" in source_metadata:
                template = source_metadata
    result = kern_to_mini(
        Path(args.input).read_text(encoding="utf-8"),
        template=template,
        meter=args.meter,
        keynote=args.keynote,
        lyric_sidecar=sidecar,
    )
    json_dump_payload(result.payload, args.out)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
