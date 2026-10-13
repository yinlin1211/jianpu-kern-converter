# MiniUnSongData Adapter

The adapter accepts the `phrase.json` shape used by the existing Jianpu
pipeline:

```json
{
  "songdata": {
    "song_name": "sample",
    "keynote": "1=C",
    "rhythm": "4/4",
    "bpm": 90,
    "notation": "...",
    "lyric": "[\"歌词\"]"
  },
  "provenance": {},
  "notes": [
    {
      "content": "1",
      "octave": 0,
      "underBarCount": 1,
      "dotCount": 0,
      "lyric": "字"
    }
  ],
  "verification": {}
}
```

`notes` is the structured source layer. `content: "0"` is a rest, `yan`
extends the preceding event by one quarter-note beat, and `jie`/`skip` is
metadata-only. Durations are converted to exact `Fraction` values.

## CLI

MiniUnSongData to single-spine Kern:

```bash
python3 tools/mini_unisongdata_cli.py to-kern \
  sample.phrase.json \
  --out sample.krn \
  --sidecar sample.kern-sidecar.json
```

Kern back to a renderable MiniUnSongData payload:

```bash
python3 tools/mini_unisongdata_cli.py from-kern \
  sample.krn \
  --template sample.phrase.json \
  --sidecar sample.kern-sidecar.json \
  --out recovered.phrase.json
```

The Python API is:

```python
from jianpu_kern import kern_to_mini, mini_to_kern, mini_to_timeline

score = load_mini_unisongdata("sample.phrase.json")
to_kern = mini_to_kern(score)
back = kern_to_mini(
    to_kern.kern,
    template=score,
    lyric_sidecar=to_kern.lyric_sidecar,
)
```

## Lossless Boundary

The exact semantic comparison is:

```text
(kind, midi, absolute_onset_beat, duration_beat)
```

This is lossless for supported monophonic event timelines when all values are
representable by the two formats. `Fraction` is retained internally.

Kern does not carry MiniUnSongData's original notation spelling, provenance,
source note IDs, or lyrics. The adapter therefore emits an explicit sidecar
with lyric/event alignment and source metadata. A regenerated Mini payload
can preserve the supplied template metadata, but it is a canonical notation
reconstruction, not a byte-for-byte copy of the original notation.

For source notation with connections, the adapter reads the official local
runtime through `runtime_dir` or `MINIUNISONGDATA_RUNTIME`. It writes Kern ties
and slurs and adds `!mini-content:note/rest/yan` local comments to distinguish
numeric tie heads from sustain dashes. Standard Kern renderers ignore these
comments; the reverse adapter uses them to preserve lyric slots. Removing
the comments loses that spelling distinction.

Rendering validation must compare the official parser's lyric text, onset,
and MIDI pitch in addition to the adapter's sidecar. An unchanged lyric
string alone does not prove correct alignment after the notation is rebuilt.

The integer `[midi, bim, dur_q]` model interface is separate and may quantize
triplet timing. Use the exact timeline API for verification.
