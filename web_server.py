#!/usr/bin/env python3
"""Local MiniUnSongData renderer and Jianpu <-> Kern demo server."""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from fractions import Fraction
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
EVENT_BRIDGE = WEB_DIR / "extract_events.js"
MAX_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_PAPER_WIDTH = 1000
GUOFENG_DATA_DIR = Path("/mnt/ssd/lian/白马入芦花janpu/10074new")
GUOFENG_CASE_DIRS = (
    "7857_招摇",
    "193919_不谓侠",
    "6001_情霜",
    "4202_长安忆",
    "5137_锦鲤抄",
    "7031_仙才叹",
    "3797_赤伶",
    "6227_苏幕遮",
    "6750_万古生香",
    "6253_思凡",
    "5948_青丝",
    "131622_无人诗",
    "3820_悦神",
    "87676_免我蹉跎苦",
    "3765_红昭愿",
    "59411_伶人",
    "7057_献天缘",
    "6274_世末歌者",
    "5622_梦回还",
    "3613_辞九门回忆",
    "5603_梅坞寻茶",
    "91463_青玉恋",
    "15724_世伪知贤",
    "6708_晚夜微雨问海棠",
    "5996_千秋令",
    "4241_春庭雪",
    "7107_夕山谣",
    "4992_红豆词",
    "6133_三生三世",
    "96447_少年如故",
)

sys.path.insert(0, str(ROOT))
from jianpu_kern import (  # noqa: E402
    jianpu_to_kern,
    kern_to_mini,
    kern_to_timeline,
    mini_to_kern,
    mini_to_timeline,
)
from tools.render_staff import render_kern_svg  # noqa: E402


DEGREE_NAMES = {
    0: ("1", ""),
    1: ("1", "#"),
    2: ("2", ""),
    3: ("2", "#"),
    4: ("3", ""),
    5: ("4", ""),
    6: ("4", "#"),
    7: ("5", ""),
    8: ("5", "#"),
    9: ("6", ""),
    10: ("6", "#"),
    11: ("7", ""),
}
KEY_TO_MIDI = {
    "C": 60,
    "#C": 61,
    "bD": 61,
    "D": 62,
    "#D": 63,
    "bE": 63,
    "E": 64,
    "F": 65,
    "#F": 66,
    "bG": 66,
    "G": 67,
    "#G": 68,
    "bA": 68,
    "A": 69,
    "#A": 70,
    "bB": 70,
    "B": 71,
}
KEY_TOKEN_RE = r"(?:[#b]?[A-Ga-g]|[A-Ga-g][#b])"


class UserInputError(ValueError):
    """An input or supported-scope error safe to show in the browser."""


def _songdata(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("songdata"), dict):
        return payload["songdata"]
    if isinstance(payload.get("song_data"), dict):
        return payload["song_data"]
    return payload


def normalize_input(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
    text = raw.strip().lstrip("\ufeff")
    if not text:
        raise UserInputError("请输入 MiniUnSongData JSON 或 notation 文本。")

    # HC notation commonly starts with tags such as `{bpm:90}`. Only a
    # quoted JSON object prefix is unambiguously JSON; repeat notation may
    # also start with `[`, so it must remain text.
    is_json_candidate = bool(re.match(r'^\{\s*"', text))
    if is_json_candidate:
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UserInputError(f"JSON 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列。") from exc
        if not isinstance(decoded, dict):
            raise UserInputError("JSON 顶层必须是对象，不能是数组。")
        payload = copy.deepcopy(decoded)
        if "songdata" not in payload and "song_data" not in payload:
            payload = {"songdata": payload}
    else:
        payload = {
            "songdata": {
                "song_name": "Manual notation",
                "rhythm": "4/4",
                "keynote": "1=C",
                "notation": text,
                "lyric": "[]",
            }
        }

    songdata = _songdata(payload)
    notation = songdata.get("notation")
    if not isinstance(notation, str) or not notation.strip():
        raise UserInputError("输入中没有可渲染的 songdata.notation。")

    lyric = songdata.get("lyric")
    if isinstance(lyric, list):
        songdata["lyric"] = json.dumps(lyric, ensure_ascii=False)
    elif lyric is None or lyric == "":
        songdata["lyric"] = "[]"
    elif not isinstance(lyric, str):
        raise UserInputError("songdata.lyric 必须是 JSON 字符串或字符串数组。")

    return payload, songdata


def guofeng_samples() -> list[dict[str, Any]]:
    """List the handoff cases without exposing arbitrary filesystem paths."""
    if not GUOFENG_DATA_DIR.is_dir():
        return []
    samples: list[dict[str, Any]] = []
    for directory in GUOFENG_CASE_DIRS:
        case_dir = GUOFENG_DATA_DIR / directory
        source_paths = sorted(case_dir.glob("*_简谱.phrase.json"))
        if not case_dir.is_dir() or not source_paths:
            continue
        source_path = source_paths[0]
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            songdata = _songdata(payload)
        except (OSError, json.JSONDecodeError):
            continue
        samples.append(
            {
                "id": f"10074new:{case_dir.name}/{source_path.name}",
                "directory": case_dir.name,
                "segment": source_path.name.removesuffix("_简谱.phrase.json"),
                "song_name": str(songdata.get("song_name") or case_dir.name),
                "keynote": str(songdata.get("keynote") or ""),
                "rhythm": str(songdata.get("rhythm") or ""),
                "note_count": len(payload.get("notes", [])),
            }
        )
    return samples


def load_guofeng_sample(sample_id: str) -> dict[str, Any]:
    prefix = "10074new:"
    if not sample_id.startswith(prefix):
        raise UserInputError(f"未知案例：{sample_id}")
    relative_path = sample_id[len(prefix):]
    entry = next((item for item in guofeng_samples() if item["id"] == sample_id), None)
    if entry is None or ".." in relative_path or "\\" in relative_path:
        raise UserInputError(f"未知案例：{sample_id}")
    source_path = GUOFENG_DATA_DIR / relative_path
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserInputError(f"案例读取失败：{sample_id}") from exc
    if not isinstance(payload, dict):
        raise UserInputError(f"案例格式错误：{sample_id}")
    return payload


def parse_meter(songdata: dict[str, Any], notation: str) -> tuple[int, str]:
    match = re.search(r"\{(?:time_signature|rhythm)\s*:\s*(\d+/\d+)\}", notation)
    rhythm = match.group(1) if match else str(songdata.get("rhythm") or "").strip()
    rhythm = rhythm or "4/4"
    match = re.fullmatch(r"(\d+)/(\d+)", rhythm)
    if not match:
        raise UserInputError(f"无法识别拍号 {rhythm!r}。")
    numerator, denominator = int(match.group(1)), int(match.group(2))
    if denominator != 4 or numerator <= 0:
        raise UserInputError(
            "当前 roundtrip 范围是四分音符拍轴的整数拍号（例如 4/4）；"
            f"收到 {rhythm}。单独渲染仍可使用该拍号。"
        )
    return numerator, rhythm


def parse_tonic(songdata: dict[str, Any], notation: str = "") -> tuple[int, str]:
    notation_match = re.search(
        rf"\{{(?:1\s*=\s*|key_signature\s*:\s*)({KEY_TOKEN_RE})\}}",
        notation,
    )
    raw = notation_match.group(1) if notation_match else str(songdata.get("keynote") or "1=C").strip()
    match = re.search(rf"1\s*=\s*({KEY_TOKEN_RE})", raw)
    key = match.group(1) if match else "C"
    if key[0] == "b":
        key = "b" + key[1].upper()
    elif key[0] == "#":
        key = "#" + key[1].upper()
    else:
        key = key.upper()
    if key not in KEY_TO_MIDI:
        raise UserInputError(f"暂不支持调号 {raw!r} 的 roundtrip 编码。")
    return KEY_TO_MIDI[key], key


def fraction_from_float(value: Any) -> Fraction:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise UserInputError(f"runtime 返回了无效时间值 {value!r}。")
    return Fraction(str(value)).limit_denominator(96)


def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def pitch_head(midi: int, tonic: int) -> str:
    relative = midi - tonic
    degree, accidental = DEGREE_NAMES[relative % 12]
    octave = (relative - (relative % 12)) // 12
    return degree + accidental + ("g" * octave if octave > 0 else "," * -octave)


def duration_suffix(duration: Fraction) -> str | None:
    return {
        Fraction(1, 4): "=",
        Fraction(1, 2): "_",
        Fraction(3, 4): "_.",
        Fraction(1): "",
        Fraction(3, 2): ".",
    }.get(duration)


def notation_note_parts(duration: Fraction) -> list[Fraction]:
    suffix = duration_suffix(duration)
    if suffix is not None:
        return [duration]
    parts: list[Fraction] = []
    remaining = duration
    for value in (Fraction(3, 2), Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(1, 4)):
        while remaining >= value:
            parts.append(value)
            remaining -= value
    if remaining:
        raise UserInputError(f"恢复 notation 时无法编码音符时值 {duration} 拍。")
    return parts


def notation_rest_parts(duration: Fraction) -> list[Fraction]:
    parts: list[Fraction] = []
    remaining = duration
    for value in (Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(1, 4)):
        while remaining >= value:
            parts.append(value)
            remaining -= value
    if remaining:
        raise UserInputError(f"恢复 notation 时无法编码休止时值 {duration} 拍。")
    return parts


def events_to_notation(
    events: list[tuple[Fraction, Fraction, int]],
    *,
    bpm: int,
    rhythm: str,
    meter_bpb: int,
    tonic: int,
    key: str,
) -> str:
    """Encode the exact Fraction timeline into ordinary HC notation."""
    body: list[str] = []
    cursor = Fraction(0)
    bar = Fraction(meter_bpb)

    def add_bar_if_ready() -> None:
        if cursor > 0 and cursor % bar == 0 and (not body or body[-1] != "|"):
            body.append("|")

    def emit_rests(duration: Fraction) -> None:
        nonlocal cursor
        for part in notation_rest_parts(duration):
            body.append("0" + (duration_suffix(part) or ""))
            cursor += part
            add_bar_if_ready()

    def emit_note(onset: Fraction, duration: Fraction, midi: int) -> None:
        nonlocal cursor
        head = pitch_head(midi, tonic)
        pieces: list[Fraction] = []
        starts: list[Fraction] = []
        remaining = duration
        piece_cursor = onset
        while remaining:
            to_bar = bar - (piece_cursor % bar)
            piece_duration = min(remaining, to_bar)
            for part in notation_note_parts(piece_duration):
                pieces.append(part)
                starts.append(piece_cursor)
                piece_cursor += part
            remaining -= piece_duration
        if len(pieces) > 1:
            body.append("(")
        for index, part in enumerate(pieces):
            part_suffix = duration_suffix(part)
            if part_suffix is not None:
                body.append(head + part_suffix)
            elif part >= 2 and part.denominator == 1:
                body.append(head if index == 0 else "-")
                body.extend("-" for _ in range(int(part) - 2))
            else:
                raise UserInputError(f"恢复 notation 时无法编码音符片段 {part} 拍。")
            if starts[index] + part == bar * ((starts[index] // bar) + 1):
                body.append("|")
        if len(pieces) > 1:
            body.append(")")
        cursor = onset + duration
        add_bar_if_ready()

    index = 0
    while index < len(events):
        onset, duration, midi = events[index]
        if onset < cursor:
            raise UserInputError(
                f"runtime 事件存在重叠：音符起点 {onset} 小于当前时间 {cursor}。"
            )
        if onset > cursor:
            emit_rests(onset - cursor)

        is_triplet = (
            duration == Fraction(1, 3)
            and index + 2 < len(events)
            and events[index + 1][0] == onset + Fraction(1, 3)
            and events[index + 2][0] == onset + Fraction(2, 3)
            and events[index + 1][1] == Fraction(1, 3)
            and events[index + 2][1] == Fraction(1, 3)
        )
        if is_triplet:
            body.append("(3:")
            for _, _, triplet_midi in events[index : index + 3]:
                body.append(pitch_head(triplet_midi, tonic) + "_")
            body.append(")")
            cursor = onset + 1
            add_bar_if_ready()
            index += 3
            continue

        emit_note(onset, duration, midi)
        index += 1

    return "\n".join(
        [
            f"{{bpm:{bpm}}}",
            f"{{key_signature:{key}}}",
            f"{{time_signature:{rhythm}}}",
            " ".join(body),
        ]
    ) + "\n"


def command_error(result: subprocess.CompletedProcess[str], label: str) -> UserInputError:
    detail = (result.stderr or result.stdout or "").strip()
    if len(detail) > 3000:
        detail = detail[-3000:]
    return UserInputError(f"{label}失败：{detail or '没有错误详情'}")


def render_json(runtime_dir: Path, payload: dict[str, Any], work_dir: Path, stem: str) -> str:
    input_path = work_dir / f"{stem}.json"
    output_path = work_dir / f"{stem}.svg"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    render_script = runtime_dir / "render_svg.js"
    if not render_script.is_file():
        raise UserInputError(f"runtime 目录中没有 render_svg.js：{runtime_dir}")
    try:
        result = subprocess.run(
            ["node", str(render_script), str(input_path), str(output_path), str(DEFAULT_PAPER_WIDTH)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UserInputError("找不到 node，请先安装 Node.js。") from exc
    except subprocess.TimeoutExpired as exc:
        raise UserInputError("官方 SVG 渲染超过 180 秒，已停止。") from exc
    if result.returncode != 0:
        raise command_error(result, "官方 SVG 渲染")
    if not output_path.is_file():
        raise UserInputError("官方 renderer 没有生成 SVG 文件。")
    return output_path.read_text(encoding="utf-8")


def extract_events(runtime_dir: Path, payload: dict[str, Any], work_dir: Path) -> list[tuple[Fraction, Fraction, int]]:
    input_path = work_dir / "events_input.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if not EVENT_BRIDGE.is_file():
        raise UserInputError(f"缺少事件解析桥：{EVENT_BRIDGE}")
    try:
        result = subprocess.run(
            [
                "node",
                str(EVENT_BRIDGE),
                "--runtime-dir",
                str(runtime_dir),
                "--input",
                str(input_path),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UserInputError("找不到 node，请先安装 Node.js。") from exc
    except subprocess.TimeoutExpired as exc:
        raise UserInputError("官方 parser 解析超过 180 秒，已停止。") from exc
    if result.returncode != 0:
        raise command_error(result, "官方 parser 解析")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise UserInputError("事件解析桥返回了无效 JSON。") from exc

    events: list[tuple[Fraction, Fraction, int]] = []
    for event in data.get("events", []):
        onset = fraction_from_float(event.get("onset"))
        duration = fraction_from_float(event.get("duration"))
        midi = int(event.get("midi"))
        if duration > 0:
            events.append((onset, duration, midi))
    if not events:
        raise UserInputError("notation 中没有可用于 roundtrip 的主旋律音符。")
    return events


def roundtrip(runtime_dir: Path, payload: dict[str, Any], songdata: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("notes"), list):
        return roundtrip_phrase(runtime_dir, payload)

    notation = str(songdata["notation"])
    meter_bpb, rhythm = parse_meter(songdata, notation)
    tonic, key = parse_tonic(songdata, notation)
    bpm_value = songdata.get("bpm", 90)
    try:
        bpm = int(float(bpm_value))
    except (TypeError, ValueError) as exc:
        raise UserInputError(f"无法识别 BPM：{bpm_value!r}。") from exc

    with tempfile.TemporaryDirectory(prefix="jianpu-kern-web-") as temp_name:
        work_dir = Path(temp_name)
        source_svg = render_json(runtime_dir, payload, work_dir, "source")
        events = extract_events(runtime_dir, payload, work_dir)
        kern = jianpu_to_kern(events, meter_bpb=meter_bpb)
        recovered_events = kern_to_timeline(kern, bar=Fraction(meter_bpb))
        exact = events == recovered_events
        if not exact:
            raise UserInputError(
                "Mini -> Kern -> Mini 事件不一致："
                f"原始 {len(events)} 个事件，恢复 {len(recovered_events)} 个事件。"
            )

        recovered_notation = events_to_notation(
            recovered_events,
            bpm=bpm,
            rhythm=rhythm,
            meter_bpb=meter_bpb,
            tonic=tonic,
            key=key,
        )
        recovered_payload = copy.deepcopy(payload)
        recovered_songdata = _songdata(recovered_payload)
        recovered_songdata["notation"] = recovered_notation
        recovered_svg = render_json(runtime_dir, recovered_payload, work_dir, "recovered")
        kern_path = work_dir / "score.krn"
        staff_svg_path = work_dir / "score_staff.svg"
        kern_path.write_text(kern + ("\n" if not kern.endswith("\n") else ""), encoding="utf-8")
        try:
            staff_svg = render_kern_svg(kern_path, staff_svg_path)
        except RuntimeError as exc:
            raise UserInputError(str(exc)) from exc

    return {
        "source_svg": source_svg,
        "recovered_svg": recovered_svg,
        "staff_svg": staff_svg,
        "kern": kern + ("\n" if not kern.endswith("\n") else ""),
        "event_count": len(events),
        "exact": exact,
        "meter": rhythm,
        "source_events": [
            {"onset": fraction_text(onset), "duration": fraction_text(duration), "midi": midi}
            for onset, duration, midi in events
        ],
        "recovered_json": recovered_payload,
    }


def roundtrip_phrase(runtime_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Roundtrip structured MiniUnSongData notes and render all three scores."""
    converted = mini_to_kern(payload, runtime_dir=runtime_dir)
    source_events = converted.events
    recovered = kern_to_mini(
        converted.kern,
        template=payload,
        meter=converted.meter,
        lyric_sidecar=converted.lyric_sidecar,
    )
    recovered_events = mini_to_timeline(recovered.payload)

    event_signature = lambda events: [
        (event.kind, event.onset, event.duration, event.midi) for event in events
    ]
    exact = event_signature(source_events) == event_signature(recovered_events)
    if not exact:
        raise UserInputError(
            "Mini → Kern → Mini 事件不一致："
            f"原始 {len(source_events)} 个事件，恢复 {len(recovered_events)} 个事件。"
        )

    with tempfile.TemporaryDirectory(prefix="jianpu-kern-web-") as temp_name:
        work_dir = Path(temp_name)
        source_svg = render_json(runtime_dir, payload, work_dir, "source")
        recovered_svg = render_json(runtime_dir, recovered.payload, work_dir, "recovered")
        kern_path = work_dir / "score.krn"
        staff_svg_path = work_dir / "score_staff.svg"
        kern_path.write_text(converted.kern + "\n", encoding="utf-8")
        try:
            staff_svg = render_kern_svg(
                kern_path,
                staff_svg_path,
                lyrics={event.onset: event.lyric for event in converted.events if event.kind == "note"},
            )
        except RuntimeError as exc:
            raise UserInputError(str(exc)) from exc

    return {
        "source_svg": source_svg,
        "recovered_svg": recovered_svg,
        "staff_svg": staff_svg,
        "kern": converted.kern + ("\n" if not converted.kern.endswith("\n") else ""),
        "event_count": len(source_events),
        "exact": exact,
        "meter": converted.meter.text,
        "source_events": [
            {
                "kind": event.kind,
                "onset": fraction_text(event.onset),
                "duration": fraction_text(event.duration),
                "midi": event.midi,
                "lyric": event.lyric,
            }
            for event in source_events
        ],
        "lyric_sidecar": [entry.as_dict() for entry in converted.lyric_sidecar],
        "recovered_json": recovered.payload,
    }


class AppHandler(BaseHTTPRequestHandler):
    server_version = "JianpuKernWeb/1.0"

    @property
    def app(self) -> "AppServer":
        return self.server  # type: ignore[return-value]

    def _send_bytes(self, body: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def do_GET(self) -> None:
        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        if path == "/api/config":
            runtime = self.app.runtime_dir
            self._send_json(
                {
                    "ok": True,
                    "runtime_configured": runtime is not None,
                    "runtime_dir": str(runtime) if runtime else "",
                    "render_script": bool(runtime and (runtime / "render_svg.js").is_file()),
                }
            )
            return
        if path == "/api/sample":
            sample_id = query.get("case", [""])[0]
            if sample_id:
                try:
                    self._send_json({"ok": True, "payload": load_guofeng_sample(sample_id)})
                except UserInputError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            sample_path = ROOT / "examples" / "real_mini_unisongdata" / "野百合也有春天_句1-2.phrase.json"
            try:
                sample = json.loads(sample_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                self._send_json({"ok": False, "error": f"无法读取真实样例：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"ok": True, "payload": sample})
            return
        if path == "/api/samples":
            self._send_json({"ok": True, "samples": guofeng_samples()})
            return
        static_names = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/styles.css": "styles.css",
        }
        filename = static_names.get(path)
        if filename is None:
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        file_path = WEB_DIR / filename
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }[file_path.suffix]
        self._send_bytes(file_path.read_bytes(), content_type)

    def do_POST(self) -> None:
        if self.path not in ("/api/render", "/api/roundtrip"):
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json({"ok": False, "error": "请求体为空或超过 8 MB 限制。"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            raw = request.get("input") if isinstance(request, dict) else None
            if not isinstance(raw, str):
                raise UserInputError("请求必须包含 input 字符串。")
            payload, songdata = normalize_input(raw)
            runtime_dir = self.app.runtime_dir
            if runtime_dir is None:
                raise UserInputError(
                    "未配置官方 runtime。请用 --runtime-dir 或 MINIUNISONGDATA_RUNTIME 启动服务。"
                )
            if self.path == "/api/render":
                with tempfile.TemporaryDirectory(prefix="jianpu-kern-web-") as temp_name:
                    svg = render_json(runtime_dir, payload, Path(temp_name), "render")
                response: dict[str, Any] = {
                    "ok": True,
                    "svg": svg,
                    "title": str(songdata.get("song_name") or "Manual notation"),
                }
            else:
                response = {"ok": True, **roundtrip(runtime_dir, payload, songdata)}
            self._send_json(response)
        except (UserInputError, OSError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # Keep the local UI useful for unexpected runtime errors.
            self._send_json({"ok": False, "error": f"服务器错误：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"[web] {self.address_string()} {format % args}\n")


class AppServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runtime_dir: Path | None):
        super().__init__(address, AppHandler)
        self.runtime_dir = runtime_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Local MiniUnSongData and Jianpu/Kern demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--runtime-dir",
        default=os.environ.get("MINIUNISONGDATA_RUNTIME", ""),
        help="directory containing the existing official render_svg.js runtime",
    )
    args = parser.parse_args()
    runtime_dir = Path(args.runtime_dir).expanduser().resolve() if args.runtime_dir else None
    if runtime_dir is not None and not runtime_dir.is_dir():
        parser.error(f"runtime directory does not exist: {runtime_dir}")
    if not WEB_DIR.is_dir():
        parser.error(f"web directory does not exist: {WEB_DIR}")

    server = AppServer((args.host, args.port), runtime_dir)
    runtime_text = str(runtime_dir) if runtime_dir else "not configured"
    print(f"Jianpu/Kern web: http://{args.host}:{args.port}/")
    print(f"Official MiniUnSongData runtime: {runtime_text}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
