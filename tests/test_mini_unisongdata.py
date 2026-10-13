from __future__ import annotations

import json
import unittest
from fractions import Fraction

from jianpu_kern.from_kern import kern_to_timeline
from jianpu_kern.mini_unisongdata import (
    Meter,
    MiniRepresentabilityError,
    degree_to_midi,
    kern_to_mini,
    mini_to_kern,
    mini_to_timeline,
)


def mini_payload(rhythm: str = "4/4") -> dict:
    return {
        "songdata": {
            "song_name": "adapter test",
            "keynote": "1=C",
            "rhythm": rhythm,
            "bpm": 90,
            "notation": "",
        },
        "provenance": {"source": "test", "keep": True},
        "notes": [
            {
                "source_id": 1,
                "measure": 1,
                "content": "1",
                "scale": 1,
                "octave": 0,
                "underBarCount": 0,
                "dotCount": 0,
                "isTiedNote": False,
                "lyric": "甲",
            },
            {
                "source_id": 2,
                "measure": 1,
                "content": "0",
                "scale": 0,
                "octave": 0,
                "underBarCount": 1,
                "dotCount": 0,
                "isTiedNote": False,
                "lyric": None,
            },
            {
                "source_id": 3,
                "measure": 1,
                "content": "2",
                "scale": 2,
                "octave": 0,
                "underBarCount": 1,
                "dotCount": 1,
                "isTiedNote": False,
                "lyric": "乙",
            },
            {
                "source_id": 4,
                "measure": 1,
                "content": "yan",
                "scale": 0,
                "octave": 0,
                "underBarCount": 0,
                "dotCount": 0,
                "isTiedNote": False,
                "lyric": None,
            },
        ],
    }


class MiniUnSongDataAdapterTest(unittest.TestCase):
    def test_fraction_duration_and_yan_are_exact(self) -> None:
        events = mini_to_timeline(mini_payload())
        self.assertEqual(
            [(event.kind, event.onset, event.duration, event.midi) for event in events],
            [
                ("note", Fraction(0), Fraction(1), 60),
                ("rest", Fraction(1), Fraction(1, 2), None),
                ("note", Fraction(3, 2), Fraction(7, 4), 62),
            ],
        )
        self.assertEqual(events[-1].source_indices, (2, 3))

    def test_content_rest_field_and_skip(self) -> None:
        payload = mini_payload()
        payload["notes"] = [
            {
                "content": "rest",
                "rest": True,
                "underBarCount": 2,
                "dotCount": 0,
            },
            {"content": "jie", "underBarCount": 0, "dotCount": 0},
            {
                "content": "1",
                "underBarCount": 0,
                "dotCount": 0,
                "lyric": "a",
            },
        ]
        events = mini_to_timeline(payload)
        self.assertEqual([(e.kind, e.onset, e.duration) for e in events], [
            ("rest", Fraction(0), Fraction(1, 4)),
            ("note", Fraction(1, 4), Fraction(1)),
        ])

    def test_to_kern_uses_existing_timeline_and_sidecar(self) -> None:
        result = mini_to_kern(mini_payload())
        recovered = kern_to_timeline(result.kern, bar=Fraction(4))
        expected = [
            (event.onset, event.duration, event.midi)
            for event in result.events
            if event.kind == "note"
        ]
        self.assertEqual(recovered, expected)
        self.assertEqual(
            [entry.as_dict() for entry in result.lyric_sidecar],
            [
                {"event_index": 0, "lyric": "甲"},
                {"event_index": 2, "lyric": "乙"},
            ],
        )
        self.assertIn("*M4/4", result.kern)

    def test_six_eight_preserves_header_and_bar_length(self) -> None:
        payload = mini_payload("6/8")
        payload["notes"] = [
            {"content": "1", "underBarCount": 0, "dotCount": 0},
            {"content": "2", "underBarCount": 0, "dotCount": 0},
            {"content": "3", "underBarCount": 0, "dotCount": 0},
        ]
        result = mini_to_kern(payload)
        self.assertEqual(result.meter, Meter(6, 8))
        self.assertIn("*M6/8", result.kern)
        self.assertEqual(
            kern_to_timeline(result.kern, bar=result.meter.bar_beats),
            [
                (Fraction(0), Fraction(1), 60),
                (Fraction(1), Fraction(1), 62),
                (Fraction(2), Fraction(1), 64),
            ],
        )

    def test_kern_to_mini_preserves_metadata_and_marks_lyrics_as_sidecar(self) -> None:
        source = mini_payload()
        source["songdata"]["lyric"] = '["旧歌词"]'
        kern_result = mini_to_kern(source)
        result = kern_to_mini(
            kern_result.kern,
            template=source,
            lyric_sidecar=kern_result.lyric_sidecar,
        )
        self.assertEqual(result.payload["songdata"]["song_name"], "adapter test")
        self.assertEqual(result.payload["provenance"], source["provenance"])
        self.assertEqual(result.payload["songdata"]["lyric"], '["旧歌词"]')
        self.assertFalse(result.payload["conversion"]["lyrics_in_kern"])
        self.assertEqual(
            result.payload["conversion"]["lyric_sidecar"],
            [{"event_index": 0, "lyric": "甲"}, {"event_index": 2, "lyric": "乙"}],
        )
        regenerated = mini_to_timeline(result.payload)
        self.assertEqual(
            [(e.onset, e.duration, e.midi) for e in regenerated if e.kind == "note"],
            [
                (e.onset, e.duration, e.midi)
                for e in result.events
                if e.kind == "note"
            ],
        )
        self.assertEqual(result.payload["notes"][0]["lyric"], "甲")
        self.assertFalse(result.payload["notes"][0]["isTiedNote"])
        self.assertTrue(any(note["isTiedNote"] for note in result.payload["notes"]))
        self.assertIn("(", result.payload["songdata"]["notation"])
        self.assertTrue(result.payload["songdata"]["notation"])

    def test_kern_triplet_is_not_silently_quantized(self) -> None:
        kern = "**kern\n*M4/4\n=\n12c\n12d\n12e\n==\n"
        self.assertEqual(
            kern_to_timeline(kern, bar=Fraction(4)),
            [
                (Fraction(0), Fraction(1, 3), 60),
                (Fraction(1, 3), Fraction(1, 3), 62),
                (Fraction(2, 3), Fraction(1, 3), 64),
            ],
        )
        with self.assertRaises(MiniRepresentabilityError):
            kern_to_mini(kern)

    def test_pitch_mapping_is_diatonic_and_exact(self) -> None:
        self.assertEqual(degree_to_midi(6, -1, "1=C"), 57)
        self.assertEqual(degree_to_midi(1, 0, "1=G"), 67)
        with self.assertRaises(MiniRepresentabilityError):
            kern_to_mini("**kern\n*M4/4\n=\n4c#\n==\n")

    def test_pickup_measure_keeps_source_barlines(self) -> None:
        # 千秋令 23: a one-beat pickup in 2/4 must not be regrouped by the meter grid.
        heads = [("6", 1, 1), ("5", 1, 1), ("6", 1, 2), ("5", 1, 2), ("6", 1, 2), ("1", 1, 2), ("6", 0, 3)]
        payload = mini_payload("2/4")
        payload["notes"] = [
            {"content": c, "underBarCount": u, "dotCount": 0, "measure": m, "octave": int(c == "1")}
            for c, u, m in heads
        ]
        result = mini_to_kern(payload)
        body = [line for line in result.kern.splitlines() if not line.startswith(("!", "*"))]
        self.assertEqual(body, ["8a", "8g", "=", "8a", "8g", "8a", "8cc", "=", "4a", "=="])
        recovered = kern_to_mini(result.kern, template=payload, meter=result.meter)
        self.assertIn("6_ 5_ | 6_ 5_ 6_ 1g_ | 6 |", recovered.payload["songdata"]["notation"])
        self.assertEqual([note["measure"] for note in recovered.payload["notes"]], [1, 1, 2, 2, 2, 2, 3])
        self.assertEqual(
            [(e.onset, e.duration, e.midi) for e in mini_to_timeline(recovered.payload)],
            [(e.onset, e.duration, e.midi) for e in result.events],
        )
        # The regenerated payload keeps the same boundaries on a second pass.
        self.assertEqual(mini_to_kern(recovered.payload).kern, result.kern)

    def test_kern_reverse_reads_actual_irregular_barlines(self) -> None:
        kern = "**kern\n*M2/4\n8c\n=\n4d\n4e\n=\n4f\n=\n4g\n4a\n==\n*-"
        result = kern_to_mini(kern)
        self.assertIn("1_ | 2 3 | 4 | 5 6 |", result.payload["songdata"]["notation"])
        self.assertEqual([note["measure"] for note in result.payload["notes"]], [1, 2, 2, 3, 4, 4])
        self.assertEqual(result.payload["conversion"]["barlines"], ["1/2", "5/2", "7/2", "11/2"])
        open_end = kern_to_mini("**kern\n*M2/4\n=\n4c\n4d\n=\n4e\n*-").payload
        self.assertTrue(open_end["songdata"]["notation"].rstrip().endswith("1 2 | 3"))

    def test_source_note_crossing_a_pickup_barline_splits_there(self) -> None:
        # Measure fields say the bar is at beat 1/2, not at the 2/4 grid beat 2.
        payload = mini_payload("2/4")
        payload["notes"] = [
            {"content": "1", "underBarCount": 1, "dotCount": 0, "measure": 1},
            {"content": "2", "underBarCount": 0, "dotCount": 1, "measure": 2},
            {"content": "3", "underBarCount": 1, "dotCount": 0, "measure": 2},
            {"content": "4", "underBarCount": 0, "dotCount": 0, "measure": 3},
        ]
        kern = mini_to_kern(payload).kern
        body = [line for line in kern.splitlines() if not line.startswith(("!", "*"))]
        self.assertEqual(body, ["8c", "=", "4.d", "8e", "=", "4f", "=="])
        notation = kern_to_mini(kern).payload["songdata"]["notation"]
        self.assertIn("1_ | 2. 3_ | 4 |", notation)

    def test_sidecar_json_is_serializable(self) -> None:
        result = mini_to_kern(mini_payload())
        json.dumps(result.sidecar(), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
