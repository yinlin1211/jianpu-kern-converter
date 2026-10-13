"""Lossless event-level conversion between Jianpu-style beat data and **kern."""

from .from_kern import (
    dur_of,
    infer_phase,
    kern_to_jianpu,
    kern_to_timeline,
    kern_to_triples,
    tokens_to_triples,
    triples_to_tokens,
)
from .kern_files import KernFile, parse_kern_file
from .mini_unisongdata import (
    KernToMiniResult,
    LyricEntry,
    Meter,
    MiniNote,
    MiniScore,
    MiniToKernResult,
    TimelineEvent,
    kern_to_mini,
    kern_to_mini_songdata,
    load_mini_unisongdata,
    mini_to_kern,
    mini_to_timeline,
    parse_meter,
)
from .pitch import kern_to_midi, midi_to_abc, midi_to_kern
from .to_kern import (
    jianpu_to_kern,
    note_rows,
    split_by_barline,
    split_by_duration,
)

__all__ = [
    "KernFile",
    "KernToMiniResult",
    "LyricEntry",
    "Meter",
    "MiniNote",
    "MiniScore",
    "MiniToKernResult",
    "TimelineEvent",
    "dur_of",
    "infer_phase",
    "jianpu_to_kern",
    "kern_to_mini",
    "kern_to_mini_songdata",
    "kern_to_jianpu",
    "kern_to_midi",
    "kern_to_timeline",
    "kern_to_triples",
    "midi_to_abc",
    "midi_to_kern",
    "load_mini_unisongdata",
    "mini_to_kern",
    "mini_to_timeline",
    "note_rows",
    "parse_kern_file",
    "parse_meter",
    "split_by_barline",
    "split_by_duration",
    "tokens_to_triples",
    "triples_to_tokens",
]

__version__ = "1.0.0"
