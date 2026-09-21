"""时间轴换算：把展开后的实际演奏路径按 beats 与 tempo 换算成毫秒时间轴。

语义约定
--------
* 每个小节给出正数 ``beats``；``tempo_bpm`` 为速度标记（拍/分钟）。
* 首小节必须给出速度作为初始速度；之后每当演奏路径经过带 ``tempo_bpm``
  的小节（包括反复、D.C./D.S. 折返后再次经过），该速度重新生效，
  从该小节起按新速度计时。
* 每步时长 = beats × 60000 / tempo_bpm 毫秒。

精度约定
--------
内部以 ``Fraction`` 精确累计，**不逐步取整**；只有输出 ``start_ms`` /
``end_ms`` / ``total_duration_ms`` 时才四舍五入到毫秒。因此相邻步骤共享的
边界是同一个精确值，输出后仍然首尾相接，总时长等于各步精确时长之和取整。
"""

from __future__ import annotations

import math
from fractions import Fraction

from .errors import StructuralError
from .expander import PlayedStep
from .models import TimelineMeasure, TimelineStep

_MS_PER_MINUTE = 60_000


def check_timeline_fields(measures: list[TimelineMeasure]) -> list[StructuralError]:
    """校验 beats / tempo_bpm：beats 必填且为正数，速度标记为正数，首小节必须有速度。"""
    errors: list[StructuralError] = []
    for m in measures:
        if m.beats is None:
            errors.append(
                StructuralError(
                    "BEATS_MISSING",
                    "每个小节都必须提供正数 beats",
                    m.measure,
                )
            )
        elif not math.isfinite(m.beats) or m.beats <= 0:
            errors.append(
                StructuralError(
                    "INVALID_BEATS",
                    f"beats 必须为正数：第 {m.measure} 小节的 beats={m.beats}",
                    m.measure,
                )
            )
        if m.tempo_bpm is not None and (not math.isfinite(m.tempo_bpm) or m.tempo_bpm <= 0):
            errors.append(
                StructuralError(
                    "INVALID_TEMPO",
                    f"tempo_bpm 必须为正数：第 {m.measure} 小节的 tempo_bpm={m.tempo_bpm}",
                    m.measure,
                )
            )
    if measures and measures[0].tempo_bpm is None:
        errors.append(
            StructuralError(
                "TEMPO_MISSING",
                "首小节必须给出 tempo_bpm 作为初始速度",
                measures[0].measure,
            )
        )
    return errors


def _round_ms(value: Fraction) -> int:
    """四舍五入到毫秒（仅在输出时调用，内部累计不使用）。"""
    return int(value + Fraction(1, 2))


def build_timeline(
    measures: list[TimelineMeasure], played: list[PlayedStep]
) -> tuple[list[TimelineStep], int]:
    """把展开后的演奏步骤换算成时间轴，返回 (steps, total_duration_ms)。

    调用前必须先通过 ``check_timeline_fields`` 校验，保证首小节有速度、
    所有 beats / tempo_bpm 为正数。
    """
    by_number = {m.measure: m for m in measures}
    occurrences: dict[int, int] = {}
    steps: list[TimelineStep] = []

    tempo: float | None = None
    cursor = Fraction(0)
    for s in played:
        m = by_number[s.measure]
        if m.tempo_bpm is not None:
            # 反复或 D.C./D.S. 再次经过速度标记时重新生效
            tempo = m.tempo_bpm
        assert tempo is not None and m.beats is not None  # 已由 check_timeline_fields 保证

        occurrences[s.measure] = occurrences.get(s.measure, 0) + 1
        duration = Fraction(m.beats) * _MS_PER_MINUTE / Fraction(tempo)
        start = cursor
        cursor += duration
        steps.append(
            TimelineStep(
                occurrence=occurrences[s.measure],
                measure=s.measure,
                start_ms=_round_ms(start),
                end_ms=_round_ms(cursor),
                tempo_bpm=tempo,
            )
        )

    return steps, _round_ms(cursor)
