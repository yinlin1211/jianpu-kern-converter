from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jianpu_kern.mini_unisongdata import kern_to_mini, mini_to_kern


WORKSPACE = Path(__file__).resolve().parents[2]
RUNTIME = WORKSPACE / "jp-svt/B_tools/jianpu_dataset/scripts/website_renderer/runtime"
EXPORTER = WORKSPACE / "jp-svt/B_tools/jianpu_dataset/scripts/playback_segments/export_playback_full.js"


def payload(notation: str, lyrics: str, heads: list[tuple[str, int, str | None]]) -> dict:
    return {
        "songdata": {
            "song_name": "sustain lyric regression",
            "keynote": "1=C",
            "rhythm": "4/4",
            "bpm": 90,
            "notation": notation,
            "lyric": json.dumps([lyrics], ensure_ascii=False),
        },
        "notes": [
            {
                "content": content, "scale": 0 if content == "yan" else int(content),
                "octave": 0, "underBarCount": under, "dotCount": 0,
                "isTiedNote": False, "lyric": lyric,
            }
            for content, under, lyric in heads
        ],
    }


@unittest.skipUnless(
    shutil.which("node") and RUNTIME.is_dir() and EXPORTER.is_file(),
    "requires the user's official notation runtime",
)
class RenderedRoundtripTest(unittest.TestCase):
    def export(self, score: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="mini-rendered-test-") as directory:
            source = Path(directory) / "score.json"
            source.write_text(json.dumps(score, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                ["node", str(EXPORTER), str(source)],
                capture_output=True, text=True, check=True, timeout=60,
            )
            return json.loads(result.stdout)

    def assert_rendered_roundtrip(self, source: dict, expected_content: list[str]) -> None:
        converted = mini_to_kern(source, runtime_dir=RUNTIME)
        recovered = kern_to_mini(
            converted.kern, template=source, meter=converted.meter,
            lyric_sidecar=converted.lyric_sidecar,
        ).payload
        self.assertEqual([note["content"] for note in recovered["notes"]], expected_content)
        before = self.export(source)
        after = self.export(recovered)
        fields = ("onset_beat", "duration_beat", "note_duration_beat", "midi_number", "source_content", "lyric")
        self.assertEqual(
            [[event[key] for key in fields] for event in before["events"]],
            [[event[key] for key in fields] for event in after["events"]],
        )

    def test_sustain_dashes_do_not_consume_the_following_lyric(self) -> None:
        source = payload(
            "(3 - -) 6_ 5_|", "影新殿",
            [("3", 0, "影"), ("yan", 0, None), ("yan", 0, None), ("6", 1, "新"), ("5", 1, "殿")],
        )
        self.assert_rendered_roundtrip(source, ["3", "yan", "yan", "6", "5"])

    def test_numeric_tie_continuation_keeps_its_empty_lyric_slot(self) -> None:
        source = payload(
            "(3_ 3_ - -) 6_ 5_|", "影_新殿",
            [("3", 1, "影"), ("3", 1, None), ("yan", 0, None), ("yan", 0, None), ("6", 1, "新"), ("5", 1, "殿")],
        )
        self.assert_rendered_roundtrip(source, ["3", "3", "yan", "yan", "6", "5"])

    def test_sustain_without_a_template_still_has_one_numeric_head(self) -> None:
        source = payload(
            "(3 - -) 6_ 5_|", "影新殿",
            [("3", 0, "影"), ("yan", 0, None), ("yan", 0, None), ("6", 1, "新"), ("5", 1, "殿")],
        )
        converted = mini_to_kern(source, runtime_dir=RUNTIME)
        recovered = kern_to_mini(
            converted.kern, lyric_sidecar=converted.lyric_sidecar,
        ).payload
        self.assertEqual(
            [note["content"] for note in recovered["notes"]],
            ["3", "yan", "yan", "6", "5"],
        )

    def assert_rendered_measures(self, source: dict) -> dict:
        converted = mini_to_kern(source, runtime_dir=RUNTIME)
        recovered = kern_to_mini(
            converted.kern, template=source, meter=converted.meter,
            lyric_sidecar=converted.lyric_sidecar,
        ).payload
        fields = ("measure_index", "onset_beat", "duration_beat", "midi_number", "lyric")
        before, after = self.export(source), self.export(recovered)
        self.assertEqual(
            [[event[key] for key in fields] for event in before["events"]],
            [[event[key] for key in fields] for event in after["events"]],
        )
        return recovered

    def test_pickup_measure_keeps_official_measure_grouping(self) -> None:
        # Same shape as 千秋令 struct 23: 2/4 with a one-beat pickup.
        source = payload(
            "6_ 5_|6_ 5_ 6_ 1g_|(6= 7_) 0= 5_ 3_|5_ 3_ 5_ 6_|6_ 0_ 3_ 5_|||", "金柝声浪迹北漠烟雨梦顶礼南佛",
            [(c, u, None) for c, u in [
                ("6", 1), ("5", 1), ("6", 1), ("5", 1), ("6", 1), ("1", 1), ("6", 2), ("7", 1),
                ("0", 2), ("5", 1), ("3", 1), ("5", 1), ("3", 1), ("5", 1), ("6", 1),
                ("6", 1), ("0", 1), ("3", 1), ("5", 1),
            ]],
        )
        source["songdata"]["rhythm"] = "2/4"
        source["notes"][5]["octave"] = 1
        recovered = self.assert_rendered_measures(source)
        self.assertIn(
            "6_ 5_ | 6_ 5_ 6_ 1g_ | (6= 7_) 0= 5_ 3_ | 5_ 3_ 5_ 6_ | 6_ 0_ 3_ 5_ |",
            recovered["songdata"]["notation"],
        )

    def test_irregular_source_measure_is_not_regridded(self) -> None:
        # Like 谪仙 case 06's 3 + 1 beat split inside 4/4.
        source = payload(
            "1 2 3 4|5 6 7|1g|2g 3g 4g 5g|||", "一二三四五六七八九十十一十二",
            [(c, 0, None) for c in ["1", "2", "3", "4", "5", "6", "7", "1", "2", "3", "4", "5"]],
        )
        for index in range(7, 12):
            source["notes"][index]["octave"] = 1
        recovered = self.assert_rendered_measures(source)
        self.assertIn("1 2 3 4 | 5 6 7 | 1g | 2g 3g 4g 5g |", recovered["songdata"]["notation"])

    def test_songdata_lyrics_are_used_when_stored_notes_have_no_lyrics(self) -> None:
        source = payload("1 2 3|", "春风来", [("1", 0, None), ("2", 0, None), ("3", 0, None)])
        converted = mini_to_kern(source, runtime_dir=RUNTIME)
        self.assertEqual(
            [(entry.event_index, entry.lyric) for entry in converted.lyric_sidecar],
            [(0, "春"), (1, "风"), (2, "来")],
        )
        self.assertTrue(all(note["lyric"] is None for note in source["notes"]))
