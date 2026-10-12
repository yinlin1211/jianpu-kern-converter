"""真实 kern 文件（HookTheory / SheetSage-A2S 语料）的解析与往返验证。

与 :mod:`jianpu_kern.to_kern` 处理的对象不同 —— 真实 kern 有几个本项目
「自己合成的 kern」里根本不存在的特征：

  * **多 spine**：``**kern`` ``**cdata`` ``**text`` 三列，以 TAB 分隔，
    第一列恒为 ``**kern``。取错列会拿到和弦或歌词。
  * **显式休止符** ``8r``：必须占时间（见 from_kern 的注释）。
  * ``*k[f#]`` 调号、``*G:`` 调名、``*clefG2`` 谱号、``=`` 小节线。
  * 装饰音、连音线 ``[`` ``]``、articulation。

本模块提供：

  :func:`parse_kern_file`   解析真实 kern 文件 → 结构化事件（含和弦/歌词）
  :func:`real_roundtrip`    真实 kern → 拍轴 → 再生成 kern → 再解析，逐事件比对

``real_roundtrip`` 是比「简谱自洽往返」强得多的验证：它输入的是**人写的、
带和弦和歌词的完整谱面**，而不是本项目自己合成的单 spine。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .from_kern import kern_to_timeline
from .pitch import kern_to_midi
from .to_kern import SINGLE, jianpu_to_kern, note_rows

_EPS = Fraction(1, 1000)
_NOTE_RE = re.compile(r"^(\d+\.*)([a-gA-G]+)([#n-]*)([LJ\[\]_]*)$")
_METER_RE = re.compile(r"^\*M(\d+)/(\d+)")
_KEY_RE = re.compile(r"^\*k\[(.*)\]")


@dataclass
class KernFile:
    """一个真实 kern 文件的解析结果。"""
    path: Path
    meter_bpb: int = 4
    key_sig: str | None = None
    tonic: str | None = None
    clef: str | None = None
    events: list = field(default_factory=list)        # (onset, dur, midi)
    chords: list = field(default_factory=list)       # (onset, chord_label)
    lyrics: list = field(default_factory=list)       # (onset, syllable)
    n_rests: int = 0


def _cell(row: str, idx: int) -> str:
    """取第 idx 列（TAB 分隔），越界返回空串。"""
    parts = row.split("\t")
    return parts[idx].strip() if idx < len(parts) else ""


def parse_kern_file(path: Path | str) -> KernFile:
    """解析真实 kern 文件。会自动识别 ``**kern`` spine 所在的列。"""
    path = Path(path)
    kf = KernFile(path=path)

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    # ① 找到 **kern spine 的列号（不假设它在第 0 列）
    kern_col = 0
    for ln in lines:
        if ln.startswith("**"):
            cols = [c.strip() for c in ln.split("\t")]
            if "**kern" in cols:
                kern_col = cols.index("**kern")
            break

    # ② 解释性头部
    for ln in lines:
        s = ln.split("\t")[0].strip()
        if s.startswith("*"):
            m = _METER_RE.match(s)
            if m:
                kf.meter_bpb = int(m.group(1))
            m = _KEY_RE.match(s)
            if m:
                kf.key_sig = m.group(1) or None
            if s.startswith("*clef"):
                kf.clef = s
            if re.match(r"^\*[A-Ga-g][:#]?$", s):
                kf.tonic = s[1:]
            continue

    # ③ 逐行解析。位置推进完全交给 kern_to_timeline（本项目已验证的实现），
    #    这样真实 kern 和合成 kern 走**同一条**解析路径，可比。
    #    弱起时首小节不足一整拍：先按标准长度解一遍拿到首音位置，再据此
    #    判定首小节真实长度，重解一次（否则其后所有音的锚点会整体偏移）。
    raw = "\n".join(
        "\t".join([_cell(ln, kern_col)] + ln.split("\t")[kern_col + 1:])
        for ln in lines
    )
    bar = Fraction(kf.meter_bpb)
    ev = kern_to_timeline(raw, bar=bar)
    if ev:
        rem = ev[0][0] % bar
        if rem != 0:
            fbl = bar - rem
            ev2 = kern_to_timeline(raw, bar=bar, first_bar_len=fbl)
            # 只有当重解的首音位置仍自洽时才采用（防止误判）
            if ev2 and ev2[0][0] == ev[0][0]:
                ev = ev2
    kf.events = ev
    kf.n_rests = sum(1 for ln in lines if _REST_RE.match(_cell(ln, kern_col)))

    # ④ 和弦 / 歌词（附加 spine，只统计不参与时间轴）
    for col_name in ("**cdata", "**harm", "**text"):
        idx = None
        for ln in lines:
            if ln.startswith("**"):
                cols = [c.strip() for c in ln.split("\t")]
                if col_name in cols:
                    idx = cols.index(col_name)
                break
        if idx is None:
            continue
        # 借 music21 的时间轴最省事；没有 music21 就退化成「按小节线数」
        t = Fraction(0)
        for ln in lines:
            v = _cell(ln, idx)
            if not v or v.startswith("*"):
                continue
            if v.startswith("="):
                t += Fraction(kf.meter_bpb)
                continue
            if v == ".":
                continue
            if col_name == "**text":
                if v.strip("."):
                    kf.lyrics.append((t, v))
            else:
                kf.chords.append((t, v))

    return kf


_REST_RE = re.compile(r"^(\d+\.*)(r+)")


def real_roundtrip(files, limit: int = 0) -> dict:
    """真实 kern → 拍轴 → 重新合成 kern → 再解析，逐事件比对音高/起点/时长。

    这条链路检验的是**转换器在真实输入上的行为**，而不是自洽性 ——
    自洽往返两边都由同一个 bug 生成时也会「通过」。
    """
    res = {
        "files": 0, "pitch_ok": 0, "count_ok": 0, "onset_ok": 0, "dur_ok": 0,
        "dur_event_ok": 0, "dur_event_total": 0,
        "n_events": 0, "n_rests": 0, "n_chords": 0, "n_lyrics": 0,
        "bad": [], "parse_err": [],
    }
    for i, p in enumerate(files):
        if limit and i >= limit:
            break
        try:
            kf = parse_kern_file(p)
        except Exception as e:
            res["parse_err"].append((Path(p).name, type(e).__name__, str(e)[:70]))
            continue
        if not kf.events:
            continue
        res["files"] += 1
        res["n_events"] += len(kf.events)
        res["n_rests"] += kf.n_rests
        res["n_chords"] += len(kf.chords)
        res["n_lyrics"] += len(kf.lyrics)

        # 拍轴行：拼回 to_kern 认识的 [onset_s, offset_s, midi, onset_beat, dur_beat]
        beats = [e[0] for e in kf.events]
        melody = [[float(o), float(o + d), int(m), float(o), float(d)]
                  for (o, d, m) in kf.events]

        kern2 = jianpu_to_kern(melody, kf.meter_bpb, key_sig=kf.key_sig,
                             prefer="dur")
        # to_kern 已经把首音前的空拍写成显式休止，生成结果按完整首小节解析。
        back = kern_to_timeline(kern2, bar=Fraction(kf.meter_bpb))

        want_p = [m for _, _, m in kf.events]
        got_p = [m for _, _, m in back]
        if want_p == got_p:
            res["pitch_ok"] += 1
        if len(want_p) == len(got_p):
            res["count_ok"] += 1
        if len(want_p) == len(got_p):
            res["onset_ok"] += sum(1 for a, b in zip(kf.events, back)
                                   if abs(float(a[0] - b[0])) <= float(_EPS))
            res["dur_ok"] += sum(1 for a, b in zip(kf.events, back)
                                 if abs(float(a[1] - b[1])) <= float(_EPS))
        for a, b in zip(kf.events, back):
            res["dur_event_total"] += 1
            if abs(float(a[1] - b[1])) <= float(_EPS):
                res["dur_event_ok"] += 1
            elif len(res["bad"]) < 6:
                res["bad"].append((Path(p).name, float(a[1]), float(b[1])))
    return res
