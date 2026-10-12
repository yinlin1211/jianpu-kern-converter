"""kern 音名 ⇄ MIDI。

kern（Humdrum）的八度机制和 ABC **完全不同**，这里只处理 kern：

    八度靠【字母的数量】。第一个字母定基准，之后每多一个字母 ±12 半音。
    单个小写 c = C5 (72)      单个大写 C = C4 (60)
    c=72, cc=84, ccc=96      C=60, CC=48, CCC=36

对比 ABC（不要混用）：ABC 靠大小写 + 逗号/撇号，重复字母被 music21 忽略。
    C,=48  C,,=36      c=72  c'=84  c''=96
两者都以 C4 = MIDI 60 为参照，但编码方式不可互换。
"""
from __future__ import annotations

import re

SEMI = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# 一个 kern 音符 token：时值前缀 + 音名 + 升降号 + 修饰
#   4.cc      时值 4.  音名 cc
#   16a-      时值 16  音名 a   降号
#   8A[       时值 8   音名 A   连音线开
NOTE_RE = re.compile(r"^(\d+\.*)([a-gA-G]+)([#n-]*)([LJ\[\]_]*)$")


def kern_to_midi(tok: str) -> int | None:
    """kern 音符 token → MIDI。非音符（`=`、`*kern`…）返回 None。"""
    m = NOTE_RE.match(tok)
    if not m:
        return None
    letters, acc = m.group(2), m.group(3)
    n = len(letters)
    # 单大写 = 第 3 八度，单小写 = 第 4 八度；每多一个字母再走一个八度。
    octv = (3 - (n - 1)) if letters[0].isupper() else (4 + (n - 1))
    midi = 12 * (octv + 1) + SEMI[letters[0].lower()]
    for ch in acc:
        if ch == "#":
            midi += 1
        elif ch == "-":
            midi -= 1
    return midi


def midi_to_kern(midi: int) -> str:
    """MIDI → kern 音名（不含时值前缀）。"""
    name = NAMES[midi % 12]
    letter = name[0].lower()
    acc = "#" if len(name) > 1 else ""
    # 先按【自然音】定位八度（# 号不影响八度），再挂升降号。
    natural = midi - (1 if acc == "#" else 0)
    up_base = 12 * 5 + SEMI[letter]     # 单小写的自然音
    low_base = 12 * 4 + SEMI[letter]    # 单大写的自然音
    if natural >= up_base:
        steps = (natural - up_base) // 12
        letters = letter * (steps + 1)
    else:
        steps = (low_base - natural) // 12
        letters = letter.upper() * (steps + 1)
    return f"{letters}{acc}"


def midi_to_abc(midi: int) -> str:
    """MIDI → ABC 音名（供 music21 交叉校验用）。

    ABC：大写 = C4 档，小写 = C5 档，`'` 升八度 / `,` 降八度。
    """
    name = NAMES[midi % 12]
    letter = name[0]
    acc = "^" if len(name) > 1 else ""
    base = 60 + SEMI[letter.lower()]
    octv = (midi - base) // 12
    if octv >= 1:
        body = letter.lower() + "'" * (octv - 1)
    else:
        body = letter + "," * (-octv)
    return f"{acc}{body}"