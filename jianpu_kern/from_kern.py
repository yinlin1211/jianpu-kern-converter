"""kern 主旋律谱 → 简谱拍轴（逆向）。

⚠️ 对没有显式首段休止的外部 kern，弱起（anacrusis）的相位在 kern 里【丢了】。
--------------------------------------------------------------------
kern 文本里音符的位置只相对小节线存在；如果文件从第一个音符直接开始，
就没有「第一根线之前有多少拍」这回事。此时一段以弱起开头的 kern，逆向时
必须显式给 `phase`。本项目的 `jianpu_to_kern()` 会写出首音前的显式休止，
因此它生成的文件不需要再传 `phase`。

`phase` 的定义：
    true_onset_beat = kern_onset_beat + phase
即「真实拍轴的第 0 拍，对应 kern 的第几拍」。无弱起的曲子 phase = 0。

重建时间轴的正确算法
--------------------------------------------------------------------
必须按「小节线锚点 + 位置推进」重建，**不能**把所有时值硬加起来：
后者会累积漂移。本模块用的是前者，且小节线只在【当前小节已填满】时才推进
（无条件 `pos = 下一小节线` 会把「音符还没填满本节就出现的小节线」误判成
跨节，位置整体推前 4 拍）。

连音线的三种形状都支持：
    单段      `8A`
    两段      `8A[` + `8A]`
    多段      `8A[` + `2A` + `8A]`   （中间段两边都不标）
"""
from __future__ import annotations

import re
from fractions import Fraction

from .pitch import kern_to_midi

# 一个 kern 音符行 token：时值前缀 + 音名 + 升降号 + 修饰
_NOTE_RE = re.compile(r"^(\d+\.*)([a-gA-G]+)([#n-]*)([LJ\[\]_()]*)$")
# 休止符：`4r` / `8r` / `2.r`。**必须显式识别并推进位置**。
# 早期版本的正则不含 r，`4r` 落到 `continue` 分支 —— 位置不推进，
# 后面所有音整体前移，且【不报错】。真值 kern 里休止符很常见
# （100 个 HookTheory 文件里 746 个），漏掉它会让往返静默出错。
_REST_RE = re.compile(r"^(\d+\.*)(r+)(\.*)$")
_EPS = Fraction(1, 1000)

# 0.25 拍格：1 拍 = 4 格。与 repos/vp_to_jianpu.py 口径一致，
# 这样本模块的输出能和 VocalParse 的三件套直接混用。
GRID_DEFAULT = 4


def dur_of(dtok: str) -> Fraction:
    """kern 时值前缀 → 拍数（1 拍 = 四分音符 = `4`）。

    kern 的 reciprocal 是【分母】：`4` = 四分 = 1 拍，`8` = 八分 = 0.5 拍。
    附点每加一个，时值 ×1.5（第二个附点加的是前值的一半，依此类推）。
    """
    m = re.match(r"^(\d+)(\.*)$", dtok)
    base = Fraction(4, int(m.group(1)))
    v, add = base, base
    for _ in m.group(2):
        add /= 2
        v += add
    return v


def kern_to_timeline(kern: str, bar: Fraction = Fraction(4),
                     first_bar_len: Fraction | None = None):
    """kern → [(起点拍, 时长拍, midi)]，连音线已合并，隐含休止已补齐。

    ``first_bar_len``：首小节的长度。**弱起时首小节不足一整拍**（如 4/4 里
    从第 3 拍进曲，首小节只有 1 拍）。不传则默认 ``bar``，即假定正拍开始。

    ⚠️ 只有当**第一个 token 就是音符**时 ``first_bar_len`` 才有意义。若曲首是
    休止符（如 `= 4r 4c ...`），首小节仍是满格，只是第一个音落在小节中间 ——
    这时按弱起处理会把首小节算短，导致其后所有音偏移。
    """
    notes = []
    pos = Fraction(0)
    anchor = Fraction(0)
    cur_len = Fraction(first_bar_len) if first_bar_len else bar
    started = False          # 是否已经见过第一个【音符】
    opened = None

    for raw in kern.splitlines():
        s = raw.strip()
        if not s or s.startswith("*"):
            continue
        if s.startswith("!"):                     # 全局注释
            continue
        if s == "=" or set(s) == {"="}:           # 小节线（= 或 ===）
            # 只有当【当前小节已经填满】时才跨到下一小节；跨过去后，
            # 后续每小节都是标准长度。
            if pos >= anchor + cur_len - _EPS:
                anchor += cur_len
                pos = anchor
                cur_len = bar
            continue

        # 多 spine 时只取第一列（主旋律）
        cell = s.split("\t")[0].strip()
        if cell in ("", ".", "*"):
            continue
        m = _NOTE_RE.match(cell)
        if not m:
            # 休止符：占时间但不产生音符，位置照常推进。
            mr = _REST_RE.match(cell)
            if mr:
                d = dur_of(mr.group(1))
                while pos >= anchor + cur_len - _EPS:
                    anchor += cur_len
                    cur_len = bar
                if pos < anchor:
                    pos = anchor
                pos += d
            continue

        # 第一个音符出现：若曲首是休止符，首小节仍是满格，不能用弱起长度
        if not started:
            started = True
            cur_len = bar
        while pos >= anchor + cur_len - _EPS:
            anchor += cur_len
            cur_len = bar
        if pos < anchor:
            pos = anchor

        d = dur_of(m.group(1))
        midi = kern_to_midi(cell)
        flags = m.group(4)

        # 连音线状态机：任何段先并入当前开口的线（若有），再看本段末尾
        # 是否要落库 / 开新线。判据只看本 token 有没有 [ 或 ]。
        tie_open = "[" in flags
        tie_close = "]" in flags

        if opened is None:
            if tie_open or tie_close:
                opened = {"onset": pos, "dur": d, "midi": midi}
            else:
                notes.append((pos, d, midi))
        else:
            opened["dur"] += d

        if tie_close and opened is not None:
            notes.append((opened["onset"], opened["dur"], opened["midi"]))
            opened = None
        pos += d

    return notes


def kern_to_jianpu(kern: str, meter_bpb: int = 4,
                   phase: Fraction | float = 0,
                   first_bar_len: Fraction | float | None = None,
                   grid: int = GRID_DEFAULT) -> dict:
    """kern → 简谱拍轴条目。

    返回 ``{"events": [(onset_beat, dur_beat, midi), ...], "phase": Fraction}``，
    拍数保留为 ``Fraction``（不丢精度），需要 0.25 拍格整数时用
    :func:`kern_to_triples`。

    ``phase``：真实拍轴第 0 拍对应 kern 的第几拍（见模块 docstring）。
    ``first_bar_len``：首小节长度，弱起时不足一整拍。
    """
    bar = Fraction(meter_bpb)
    ph = Fraction(phase).limit_denominator(96)
    events = []
    for onset, dur, midi in kern_to_timeline(kern, bar=bar,
                                             first_bar_len=first_bar_len):
        events.append((onset + ph, dur, midi))
    return {"events": events, "phase": ph}


def kern_to_triples(kern: str, meter_bpb: int = 4,
                    phase: Fraction | float = 0,
                    first_bar_len: Fraction | float | None = None,
                    grid: int = GRID_DEFAULT):
    """kern → [[midi, bim, dur_q], ...]，即数据集的「三件套」。

    bim  = 小节内 0.25 拍格位置（对 measure_len 取模）
    dur_q= 发声音长，0.25 拍格；与 vp_to_jianpu 一致，至少占 1 格避免 0 长音符
    """
    grid = grid or GRID_DEFAULT
    msub = int(round(meter_bpb * grid))
    out = []
    for onset, dur, midi in kern_to_jianpu(kern, meter_bpb, phase,
                                           first_bar_len)["events"]:
        bim = int(round(float(onset) * grid)) % msub
        dur_q = max(1, int(round(float(dur) * grid)))
        out.append([int(midi), bim, dur_q])
    return out


def triples_to_tokens(triples, special=("<PAD>", "<SOS>", "<EOS>")):
    """三件套 → 词表 token 串（每音 3 个 token 展平）。"""
    toks = []
    for midi, bim, dur_q in triples:
        toks += [f"P{int(midi):03d}", f"B{int(bim):02d}", f"D{int(dur_q):02d}"]
    return toks


def tokens_to_triples(tokens, special=("<PAD>", "<SOS>", "<EOS>")):
    """token 串 → 三件套。"""
    ts = [str(t) for t in tokens if str(t) not in special]
    if len(ts) % 3:
        raise ValueError(f"token 数 {len(ts)} 不是 3 的倍数，无法按 (P,B,D) 解析")
    out = []
    for i in range(0, len(ts), 3):
        p, b, d = ts[i], ts[i + 1], ts[i + 2]
        if not (p.startswith("P") and b.startswith("B") and d.startswith("D")):
            raise ValueError(f"第 {i // 3} 组不是 (P,B,D) 顺序: {ts[i:i + 3]}")
        out.append([int(p[1:]), int(b[1:]), int(d[1:])])
    return out


def infer_phase(kern: str, first_onset_beat, meter_bpb: int = 4):
    """已知真实首音拍位时，反推 kern 需要的 phase（校验 / 调试用）。"""
    raw = kern_to_timeline(kern, bar=Fraction(meter_bpb))
    if not raw:
        return Fraction(0)
    return Fraction(first_onset_beat).limit_denominator(96) - raw[0][0]
