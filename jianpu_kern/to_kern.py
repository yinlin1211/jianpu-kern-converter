"""简谱拍轴 → kern 主旋律谱（正向）。

输入是拍轴上的音符行 `[onset_sec, offset_sec, midi, onset_beat, duration_beat]`，
输出是 HookKern 认识的**单 spine** kern 文本。

kern 的两条硬约束（都实测过，违反任一条 music21 就拒绝解析）：

  1. **音符不能跨小节线** —— 跨了必须在线上切开，用连音线 `[` `]` 连起来。
     参考数据里就是这么写的：`4g[` / `=` / `16g]`
  2. **单个时值只有 2^k / 3·2^k / 7·2^k 三种家族** —— 1.25 拍(5/4)、2.5 拍(5/2)
     没有单个写法，也要拆成多段 + 连音线。

所以转一个音分三步：

  ① 按小节线切成若干「段」      [0.75, 2.0) 正好到线 → 一段
  ② 每段再按时值拆成可表示的块  段长 1.25 拍 → 1.0 + 0.25
  ③ 相邻块若音高相同就用连音线连起来；跨越小节线时线两侧的块必须连

例：onset=0.75 dur=1.75（跨过 2.0 的小节线）
    段1 [0.75, 2.00) = 1.25 拍 → 块 4 + 16
    段2 [2.00, 2.50) = 0.50 拍 → 块 8
    输出： 4X[  16X[  =  8X]

关于休止符：音符之间的空档必须写成显式 `r` token，否则 kern 解析时会把
后续音符向前压缩。本模块也会从绝对拍轴的 0 开始，为第一个音前的空档写入
休止符；复杂时值拆分仍通过连音线保留为一个事件。
"""
from __future__ import annotations

from fractions import Fraction

from .pitch import midi_to_kern

# 时值映射。
#
# 口径：**键 = 拍数**（1 拍 = 四分音符），值 = kern 的 reciprocal 前缀。
# kern 里 `4` 是四分音符 = 1 拍，`8` 是八分 = 0.5 拍，所以前缀 = 4 / 拍数。
# 早期版本直接把 kern 前缀当拍数用（1/4 → "16"），整体小了 4 倍。
SINGLE = {
    Fraction(1, 16): "64", Fraction(3, 32): "64.", Fraction(1, 8): "32",
    Fraction(3, 16): "32.", Fraction(1, 4): "16", Fraction(3, 8): "16.",
    Fraction(1, 2): "8", Fraction(3, 4): "8.", Fraction(7, 8): "8..",
    Fraction(1, 1): "4", Fraction(3, 2): "4.", Fraction(7, 4): "4..",
    Fraction(2, 1): "2", Fraction(3, 1): "2.", Fraction(7, 2): "2..",
    Fraction(4, 1): "1", Fraction(6, 1): "1.", Fraction(7, 1): "1..",
    # 三连音家族
    Fraction(1, 3): "12", Fraction(2, 3): "6", Fraction(1, 6): "24",
    Fraction(1, 12): "48", Fraction(1, 24): "96",
}
_EPS = Fraction(1, 10000)


def split_by_barline(onset: Fraction, dur: Fraction, bar: Fraction):
    """[onset, onset+dur) 按小节线切段 → [(起点, 时长), ...]"""
    segs, t, end = [], onset, onset + dur
    while t < end - _EPS:
        next_bar = (t // bar + 1) * bar
        seg_end = min(next_bar, end)
        segs.append((t, seg_end - t))
        t = seg_end
    return segs or [(onset, dur)]


def split_by_duration(dur: Fraction):
    """一个时值拆成 kern 可表示的若干块（贪心从大到小）。

    Kern 不能精确表达的分母不能被静默近似；如果剩余量不在当前词表
    能拼出的范围内，直接报错，避免生成一个看似合法但时长已经改变的谱面。
    """
    if dur in SINGLE:
        return [dur]
    out, rem = [], dur
    for v in sorted(SINGLE, reverse=True):
        while rem >= v - _EPS:
            out.append(v)
            rem -= v
    if rem > _EPS:
        raise ValueError(
            f"时值 {dur} 无法由当前 Kern 时值表精确拆分，剩余 {rem}；"
            "请扩展 SINGLE 或使用旁车有理数元数据。"
        )
    return out or [dur]


def note_rows(melody, grid_den: int = 96, prefer: str = "dur"):
    """拍轴音符行 → 规范化的 [(onset_beat, dur_beat, midi), ...]。

    ``prefer`` 决定发声音长取哪个来源：

    ``"dur"``（默认，**推荐**）
        用标注的 ``duration_beat`` 字段。真实 kern 里音后可能有一拍以上的
        休止，间隔远大于实际时值（例：onset=19、dur=2.0，下一音在 26.5 →
        间隔被算成 7.5 拍），音被无声拉长。数据集里同样是标注 dur 才对：
        「孤梦」有个 onset=12.0 的音，标注 dur=2.0，下一音在 17.0 ——
        interval 会算出 5.0 拍，把中间 3 拍休止吞进音里。

    ``"interval"``
        用「到下一个音的间隔」。仅在 dur 字段缺失/非正时作为兜底。
    """
    ns = sorted(melody, key=lambda x: x[3])
    rows = []
    for i, n in enumerate(ns):
        onset = Fraction(n[3]).limit_denominator(grid_den)
        dur = Fraction(n[4]).limit_denominator(grid_den) \
            if (prefer == "dur" and len(n) > 4) else Fraction(0)
        if dur <= 0 and i + 1 < len(ns):
            dur = Fraction(ns[i + 1][3]).limit_denominator(grid_den) - onset
        rows.append((onset, dur, int(n[2])))
    return rows


def _flat_blocks(onset: Fraction, dur: Fraction, bar: Fraction):
    """把一个音展开成 [(起点, 时长, 所属小节号), ...]。"""
    blocks = []
    for seg_onset, seg_dur in split_by_barline(onset, dur, bar):
        t = seg_onset
        for p in split_by_duration(seg_dur):
            blocks.append({"onset": t, "dur": p, "bar": t // bar})
            t += p
    return blocks

def _measure_grid(rows, bar: Fraction):
    """算出每个音属于第几小节，并给出每小节的起点拍。
    ⚠️ 弱起（anacrusis）会让**首小节不足一拍整**：首音在第 1.0 拍时，
    首小节其实是从 1.0 到 4.0，只有 3 拍。早期版本一律按「绝对 4 的整数倍」
    切小节线，于是把首小节之后的音全部切错位置 —— 往返时表现为音高/时值
    都对、只有 beat-in-measure 偏。
    这里把首小节的起点定在**首音的 onset**（而不是 0），其后每小节 bar 拍。
    """
    first = rows[0][0]
    # 严格大于 first 的最小 bar 整数倍 = 第二个完整小节的起点
    second = (first // bar + 1) * bar if (first % bar) != 0 else first + bar

    def measure_start(mi: int) -> Fraction:
        """第 mi 个小节的起点拍（mi 从 0 开始）。"""
        return first if mi == 0 else second + (mi - 1) * bar

    def measure_of(onset: Fraction) -> int:
        # 恰好落在 second（首个标准小节线）上的时点，属于**下一**小节 ——
        # 这与切段循环 `measure_start(measure_of(t) + 1)` 的前进假设一致。
        # 若把边界归入首小节，该循环会原地不动（死循环）。
        if onset < second:
            return 0
        return 1 + int((onset - second) // bar)

    return measure_start, measure_of


def jianpu_to_kern(melody, meter_bpb: int = 4, key_sig: str | None = None,
                  prefer: str = "dur") -> str:
    """简谱拍轴 → kern 文本。
    参数
    ----
    melody : 拍轴音符行，或已规范化的 (onset_beat, dur_beat, midi) 三元组
    meter_bpb : 每小节拍数（数据集 4/4 子集恒为 4）
    key_sig : 可选，Humdrum 调号如 ``"bBEB"``；给了会写 ``*k[...]`` 头
    """
    if not melody:
        return ""
    if len(melody[0]) == 5:
        rows = note_rows(melody, prefer=prefer)
    else:
        rows = [(Fraction(a).limit_denominator(96),
                 Fraction(b).limit_denominator(96), int(c))
                for a, b, c in melody]
    bar = Fraction(meter_bpb)
    measure_start, measure_of = _measure_grid(rows, bar)
    lines = ["**kern"]
    if key_sig:
        lines.append(f"*k[{key_sig}]")
    lines.append(f"*M{meter_bpb}/4")
    lines.append("=")
    cur_mi = 0            # 已经输出到第几小节的起点
    # 绝对拍轴必须从 0 开始。首音前的空拍不能省略，否则逆向无法知道
    # 首音在原始时间轴上的位置；该休止也不会影响后续的连音线拆分。
    cursor = Fraction(0)
    if rows[0][0] > _EPS:
        for part in split_by_duration(rows[0][0]):
            lines.append(f"{SINGLE[part]}r")
        cursor = rows[0][0]
    for onset, dur, midi in rows:
        # 两个音之间的休止必须显式写出来，否则位置对不上：kern 只在音符之间
        # 写休止符，缺了它解析时会认为时间连续，导致后面所有音的绝对位置前移。
        # 分三种情况：整小节空 → 小节线；小节内空 → `r`；跨小节的空 → 先补
        # 小节线把当前小节填满，再在新小节里写 `r`。
        if onset - cursor > _EPS:
            t = cursor
            while t < onset - _EPS:
                mi = measure_of(t)
                nxt = measure_start(mi + 1)
                seg_end = min(nxt, onset)
                remain = seg_end - t
                if remain > _EPS:
                    # 本小节内的空 → 显式休止符（写到小节线或到目标音为止）
                    for part in split_by_duration(remain):
                        lines.append(f"{SINGLE[part]}r")
                    t = seg_end
                    if t >= onset - _EPS:
                        break
                # 小节已填满（或没写休止就填满了）→ 输出小节线收尾本小节
                lines.append("=")
                cur_mi = mi + 1
                t = nxt

        # 按小节线把一个音切成若干「段」，每段落在一个小节内
        segs = []
        t = onset
        end = onset + dur
        while t < end - _EPS:
            nxt = measure_start(measure_of(t) + 1)
            seg_end = min(nxt, end)
            segs.append((t, seg_end - t, measure_of(t)))
            t = seg_end
        if not segs:
            segs = [(onset, dur, measure_of(onset))]

        # 音与音之间整小节的休止 → 补小节线。
        # 首小节（弱起）只有几拍，**不能**用小节线把它"填满"到标准长度。
        while cur_mi < segs[0][2]:
            lines.append("=")
            cur_mi += 1

        # 每段再按 kern 可表示的时值切块（保持段的先后顺序）
        blocks = []
        for seg_o, seg_d, seg_mi in segs:
            tt = seg_o
            for part in split_by_duration(seg_d):
                blocks.append({"onset": tt, "dur": part, "mi": seg_mi})
                tt += part

        name = midi_to_kern(midi)
        n_blk = len(blocks)
        for idx, b in enumerate(blocks):
            tok = f"{SINGLE[b['dur']]}{name}"
            if n_blk > 1 and idx == 0:
                tok += "["
            if n_blk > 1 and idx == n_blk - 1:
                tok += "]"
            lines.append(tok)

            # 块之间跨了小节线：补线（线两侧已用连音线连住）
            if idx < n_blk - 1 and blocks[idx + 1]["mi"] != b["mi"]:
                lines.append("=")
                cur_mi = blocks[idx + 1]["mi"]

        # 本音结束后，把跨过的小节线补齐
        end_mi = measure_of(end - _EPS) if end > onset else segs[-1][2]
        while cur_mi < end_mi:
            cur_mi += 1
            lines.append("=")
        cursor = onset + dur

    lines.append("==")
    return "\n".join(lines)
