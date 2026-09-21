"""静态结构分析。

把按小节排列的标记解析成：

* 反复段（Section）：|: ... :|，支持嵌套、隐式起点（曲首 :|）、
  以及由第一/第二结尾构成的 volta 反复；
* 结尾组（EndingGroup）：连续的 1./2./... 结尾构成一个 cluster，
  归属其所在的反复段；
* 导航信息：Segno / Coda / Fine / To Coda / D.C. / D.S.。

分析只收集错误与警告，不抛异常——校验接口需要一次性返回全部问题。
存在错误时展开接口拒绝执行，因此不保证错误场景下 Section 数据完全精确。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import StructuralError
from .models import Measure

# ---------------------------------------------------------------------------
# 解析结果数据结构
# ---------------------------------------------------------------------------


@dataclass
class EndingGroup:
    """一个结尾（房子），例如第一结尾。"""

    number: int
    start: int  # 位置（0 基，含）
    end: int  # 位置（0 基，含）


@dataclass
class Section:
    """一个反复段。"""

    sid: int
    start: int  # 反复体首小节位置（|: 小节本身；隐式起点时为 0）
    start_marker: int | None  # |: 位置；None 表示曲首隐式起点
    ends: list[int] = field(default_factory=list)  # 所有显式 :| 位置
    endings: dict[int, EndingGroup] = field(default_factory=dict)  # 结尾号 -> 组
    close_end: int = -1  # 反复段最后一个小节位置
    times: int = 2  # 演奏遍数
    implicit_close: bool = False  # 是否在最后一个结尾处隐式闭合

    @property
    def volta_k(self) -> int:
        return max(self.endings, default=0)


@dataclass
class Navigation:
    kind: str
    position: int
    segno: int | None = None
    coda: int | None = None
    fine: int | None = None
    to_coda: int | None = None


@dataclass
class Analysis:
    measures: list[Measure]
    sections: list[Section]
    # 显式 :| 小节位置 -> 所属反复段
    end_owner: dict[int, Section]
    # 小节位置 -> 结尾号
    ending_at: dict[int, int]
    navigation: Navigation | None
    errors: list[StructuralError]
    warnings: list[StructuralError]

    @property
    def valid(self) -> bool:
        return not self.errors

    def err(self, code: str, message: str, measure: int | None) -> None:
        self.errors.append(StructuralError(code, message, measure))

    def warn(self, code: str, message: str, measure: int | None) -> None:
        self.warnings.append(StructuralError(code, message, measure))

    def mnum(self, pos: int) -> int:
        return self.measures[pos].measure


# ---------------------------------------------------------------------------
# 分析入口
# ---------------------------------------------------------------------------


def analyze(measures: list[Measure]) -> Analysis:
    n = len(measures)
    result = Analysis(
        measures=measures,
        sections=[],
        end_owner={},
        ending_at={},
        navigation=None,
        errors=[],
        warnings=[],
    )

    groups = _build_ending_groups(result, n)
    clusters, cluster_valid = _cluster_endings(result, groups, n)
    sections = _parse_repeats(result, groups, clusters, cluster_valid, n)
    result.sections = sections
    _check_marker_placement(result, groups, n)
    _check_navigation(result, sections, n)
    return result


# ---------------------------------------------------------------------------
# 结尾（房子）解析
# ---------------------------------------------------------------------------


def _build_ending_groups(result: Analysis, n: int) -> list[EndingGroup]:
    measures = result.measures
    starts = [i for i, m in enumerate(measures) if m.ending_start]
    stops = [i for i, m in enumerate(measures) if m.ending_stop]

    groups: list[EndingGroup] = []
    used_stops: set[int] = set()

    for idx, start in enumerate(starts):
        m = measures[start]
        if not m.ending_number:
            result.err(
                "ENDING_NUMBER_MISSING",
                "结尾起始小节必须提供 ending_number",
                m.measure,
            )
        next_start = starts[idx + 1] if idx + 1 < len(starts) else n
        stop = next((s for s in stops if start <= s < next_start), None)
        if stop is not None:
            used_stops.add(stop)
            end = stop
        else:
            end = next_start - 1
        groups.append(EndingGroup(number=m.ending_number or 0, start=start, end=end))

    # 有 ending_number 但没有 ending_start
    for i, m in enumerate(measures):
        if m.ending_number and not m.ending_start:
            result.err(
                "ENDING_START_MISSING",
                "ending_number 只能标注在 ending_start 小节上",
                m.measure,
            )

    # 未被任何结尾消费的 ending_stop
    for s in stops:
        if s not in used_stops:
            result.err(
                "ENDING_STOP_ORPHAN",
                "ending_stop 不属于任何结尾段",
                measures[s].measure,
            )

    return groups


def _cluster_endings(
    result: Analysis, groups: list[EndingGroup], n: int
) -> tuple[list[list[EndingGroup]], dict[int, bool]]:
    """把位置相邻的结尾组合成 cluster；每个 cluster 的编号必须是 1..k。

    返回 (clusters, cluster id -> 是否合法)。
    """
    clusters: list[list[EndingGroup]] = []
    for g in groups:
        if clusters and clusters[-1][-1].end + 1 == g.start:
            clusters[-1].append(g)
        else:
            clusters.append([g])

    cluster_valid_flags: dict[int, bool] = {}
    for cluster in clusters:
        valid = True
        for expected, g in enumerate(cluster, start=1):
            if g.number == 0 or g.number != expected:
                result.err(
                    "ENDING_NUMBER_SEQUENCE",
                    f"结尾编号须从 1 连续递增：此处应为第 {expected} 结尾，"
                    f"实际为第 {g.number or '?'} 结尾",
                    result.mnum(g.start),
                )
                valid = False
        cluster_valid_flags[id(cluster)] = valid
    return clusters, cluster_valid_flags


# ---------------------------------------------------------------------------
# 反复段解析
# ---------------------------------------------------------------------------


@dataclass
class _Frame:
    section: Section
    # 该段拥有的结尾 cluster（按 cluster 起始位置记录）
    cluster_start: int | None = None
    cluster_groups: list[EndingGroup] = field(default_factory=list)
    cluster_valid: bool = True


def _parse_repeats(
    result: Analysis,
    groups: list[EndingGroup],
    clusters: list[list[EndingGroup]],
    cluster_valid: dict[int, bool],
    n: int,
) -> list[Section]:
    measures = result.measures
    cluster_at_start = {c[0].start: c for c in clusters}
    # 结尾位置 -> (cluster 起始位置, 编号)
    ending_info: dict[int, tuple[int, int]] = {}
    for cluster in clusters:
        for g in cluster:
            for pos in range(g.start, g.end + 1):
                ending_info[pos] = (cluster[0].start, g.number)
                result.ending_at[pos] = g.number

    frames: list[_Frame] = []
    sections: list[Section] = []
    next_sid = 0

    def open_section(start: int, start_marker: int | None) -> _Frame:
        nonlocal next_sid
        sec = Section(sid=next_sid, start=start, start_marker=start_marker)
        next_sid += 1
        sections.append(sec)
        return _Frame(section=sec)

    for i, m in enumerate(measures):
        if m.repeat_start:
            frames.append(open_section(start=i, start_marker=i))

        # 结尾 cluster 归属当前最内层未闭合反复段
        if i in cluster_at_start:
            cluster = cluster_at_start[i]
            if not frames:
                result.err(
                    "UNCLOSED_ENDING",
                    "第一/第二结尾必须位于反复段内：找不到包围它的 |: 记号",
                    measures[cluster[0].start].measure,
                )
            else:
                frame = frames[-1]
                if frame.cluster_start is not None:
                    result.err(
                        "NESTED_ENDING_UNSUPPORTED",
                        "一个反复段内只能有一组连续结尾",
                        measures[i].measure,
                    )
                else:
                    frame.cluster_start = i
                    frame.cluster_groups = cluster
                    frame.cluster_valid = cluster_valid[id(cluster)]
                    for g in cluster:
                        frame.section.endings[g.number] = g

        if m.repeat_end:
            # 嵌套 volta：含合法结尾 cluster 的内层段在最后结尾之后不再拥有自己的 :|，
            # 先让它在最后一个结尾末尾隐式闭合，本小节的 :| 归外层段所有
            while frames:
                top = frames[-1]
                if (
                    top.cluster_valid
                    and top.cluster_groups
                    and i > top.cluster_groups[-1].end
                    and not any(g.end == i for g in top.cluster_groups)
                ):
                    top.section.close_end = top.cluster_groups[-1].end
                    top.section.implicit_close = True
                    frames.pop()
                else:
                    break

            if frames:
                frame = frames[-1]
            else:
                # 曲首隐式反复：:| 之前没有 |:，反复体自第 1 小节开始
                frame = open_section(start=0, start_marker=None)
                sections.append(frame.section)

            sec = frame.section

            # 该 :| 是否为某个非最后结尾的 volta 收尾（第一结尾后的 :|）
            volta_end = bool(
                frame.cluster_groups
                and any(
                    g.end == i and g.number < len(frame.cluster_groups)
                    for g in frame.cluster_groups
                )
            )

            sec.ends.append(i)
            result.end_owner[i] = sec

            if not volta_end:
                # 反复段真正结束：显式起点段出栈；隐式起点段只闭合不出栈（栈底为空）
                sec.close_end = i
                if sec.start_marker is not None:
                    frames.pop()

    # 处理未闭合的反复段
    if frames:
        top = frames[-1]
        sec = top.section
        if top.cluster_start is not None and len(frames) == 1 and top.cluster_valid:
            # 允许的简写：最外层反复段的最后一个结尾后省略 :|，在该结尾末尾闭合
            sec.close_end = top.cluster_groups[-1].end
            sec.implicit_close = True
            frames.pop()
        else:
            result.err(
                "UNCLOSED_REPEAT",
                "存在未闭合的左反复记号 |:（缺少配对的 :|）",
                result.mnum(sec.start_marker) if sec.start_marker is not None else None,
            )
            sec.close_end = n - 1
            frames.pop()

        for frame in frames:
            s = frame.section
            s.close_end = n - 1
            result.err(
                "UNCLOSED_REPEAT",
                "存在未闭合的左反复记号 |:（缺少配对的 :|）",
                result.mnum(s.start_marker) if s.start_marker is not None else None,
            )

    _check_repeat_details(result, sections, n)
    return sections


def _check_repeat_details(result: Analysis, sections: list[Section], n: int) -> None:
    measures = result.measures

    # 孤儿 repeat_times：既不在 |: 也不在 :| 小节
    for i, m in enumerate(measures):
        if m.repeat_times is not None and not m.repeat_start and not m.repeat_end:
            result.err(
                "REPEAT_TIMES_ORPHAN",
                "repeat_times 只能写在 |: 或 :| 小节上",
                m.measure,
            )

    for sec in sections:
        candidates: list[int] = []
        if sec.start_marker is not None:
            v = measures[sec.start_marker].repeat_times
            if v is not None:
                candidates.append(v)
        for ep in sec.ends:
            v = measures[ep].repeat_times
            if v is not None:
                candidates.append(v)
        if candidates:
            if len(set(candidates)) > 1:
                result.err(
                    "REPEAT_TIMES_CONFLICT",
                    f"同一反复段在 |: 与 :| 上给出了不同的遍数：{sorted(set(candidates))}",
                    result.mnum(sec.start_marker if sec.start_marker is not None else sec.ends[0]),
                )
            sec.times = candidates[0]

        k = sec.volta_k
        if k:
            # 非最后结尾必须以 :| 收尾
            for number, g in sec.endings.items():
                if number < k and not measures[g.end].repeat_end:
                    result.err(
                        "MISSING_END_REPEAT",
                        f"第 {number} 结尾末尾必须有右反复记号 :|",
                        result.mnum(g.end),
                    )
            if sec.times != k:
                anchor = sec.start_marker if sec.start_marker is not None else sec.ends[0]
                result.err(
                    "REPEAT_TIMES_ENDING_MISMATCH",
                    f"反复段声明演奏 {sec.times} 遍，但给出了 {k} 个结尾，二者必须一致",
                    result.mnum(anchor),
                )


# ---------------------------------------------------------------------------
# 标记摆放检查
# ---------------------------------------------------------------------------


def _check_marker_placement(
    result: Analysis, groups: list[EndingGroup], n: int
) -> None:
    measures = result.measures
    in_ending = {pos for g in groups for pos in range(g.start, g.end + 1)}

    for i, m in enumerate(measures):
        # 结尾小节内禁止跳转类标记与 |:
        if i in in_ending:
            if m.repeat_start:
                result.err(
                    "REPEAT_START_INSIDE_ENDING",
                    "左反复记号 |: 不能出现在结尾（房子）小节内",
                    m.measure,
                )
            for flag, name in (
                (m.segno, "Segno"),
                (m.coda, "Coda"),
                (m.fine, "Fine"),
                (m.to_coda, "To Coda"),
                (m.navigation is not None, "D.C./D.S."),
            ):
                if flag:
                    result.err(
                        "MARKER_INSIDE_ENDING",
                        f"{name} 标记不能出现在结尾（房子）小节内",
                        m.measure,
                    )

        # 反复小节杠上禁止 Fine / To Coda / D.C./D.S. / Coda
        if m.repeat_start or m.repeat_end:
            for flag, name in (
                (m.fine, "Fine"),
                (m.to_coda, "To Coda"),
                (m.coda, "Coda"),
                (m.navigation is not None, "D.C./D.S."),
            ):
                if flag:
                    result.err(
                        "MARKER_ON_REPEAT_BAR",
                        f"{name} 标记不能与反复记号写在同一小节，请单独放置",
                        m.measure,
                    )

        if m.fine and m.to_coda:
            result.err(
                "CONFLICTING_MARKERS",
                "Fine 与 To Coda 不能出现在同一小节",
                m.measure,
            )


# ---------------------------------------------------------------------------
# 导航标记检查
# ---------------------------------------------------------------------------

_FINE_KINDS = {"dc_al_fine", "ds_al_fine", "dc", "ds"}
_CODA_KINDS = {"dc_al_coda", "ds_al_coda"}


def _positions(measures: list[Measure], attr: str) -> list[int]:
    return [i for i, m in enumerate(measures) if getattr(m, attr)]


def _check_navigation(result: Analysis, sections: list[Section], n: int) -> None:
    measures = result.measures
    navs = [(i, m.navigation) for i, m in enumerate(measures) if m.navigation]
    segnos = _positions(measures, "segno")
    codas = _positions(measures, "coda")
    fines = _positions(measures, "fine")
    to_codas = _positions(measures, "to_coda")

    if len(navs) > 1:
        for i, _ in navs[1:]:
            result.err(
                "MULTIPLE_NAVIGATION",
                "一首乐曲只能有一个 D.C./D.S. 跳转指令",
                result.mnum(i),
            )

    # 重复的同类目标点
    if len(segnos) > 1:
        result.err("AMBIGUOUS_SEGNO", "存在多个 Segno 标记，跳转目标不唯一", result.mnum(segnos[1]))
    if len(codas) > 1:
        result.err("AMBIGUOUS_CODA", "存在多个 Coda 标记，跳转目标不唯一", result.mnum(codas[1]))
    if len(fines) > 1:
        result.err("AMBIGUOUS_FINE", "存在多个 Fine 标记，结束位置不唯一", result.mnum(fines[1]))
    if len(to_codas) > 1:
        result.err(
            "AMBIGUOUS_TO_CODA",
            "存在多个 To Coda 标记，跳转位置不唯一",
            result.mnum(to_codas[1]),
        )

    if not navs:
        if fines:
            result.warn(
                "UNUSED_FINE",
                "Fine 标记存在，但乐曲没有 D.C./D.S. 指令，Fine 不会生效",
                result.mnum(fines[0]),
            )
        if to_codas:
            result.err(
                "TO_CODA_WITHOUT_NAVIGATION",
                "To Coda 必须配合 D.C./D.S. al Coda 使用",
                result.mnum(to_codas[0]),
            )
        if codas:
            result.warn(
                "UNUSED_CODA",
                "Coda 标记存在，但乐曲没有 al Coda 跳转，Coda 不会被进入",
                result.mnum(codas[0]),
            )
        if segnos:
            result.warn(
                "UNUSED_SEGNO",
                "Segno 标记存在，但乐曲没有 D.S. 指令",
                result.mnum(segnos[0]),
            )
        return

    nav_pos, kind = navs[0]
    nav = Navigation(kind=kind, position=nav_pos)
    result.navigation = nav

    # D.S. 需要 Segno
    if kind.startswith("ds"):
        if not segnos:
            result.err(
                "MISSING_SEGNO",
                f"{kind.upper()} 需要一个 Segno 目标标记",
                result.mnum(nav_pos),
            )
        else:
            nav.segno = segnos[0]
            if segnos[0] >= nav_pos:
                result.err(
                    "SEGNO_AFTER_NAVIGATION",
                    "Segno 必须位于 D.S. 指令之前",
                    result.mnum(segnos[0]),
                )

    # 需要 Fine 的指令
    if kind in _FINE_KINDS:
        if not fines:
            result.err(
                "MISSING_FINE",
                f"{kind.upper()} 需要一个 Fine 标记",
                result.mnum(nav_pos),
            )
        else:
            nav.fine = fines[0]
            if fines[0] >= nav_pos:
                result.err(
                    "FINE_AFTER_NAVIGATION",
                    "Fine 必须位于 D.C./D.S. 指令之前",
                    result.mnum(fines[0]),
                )

    # al Coda 需要 To Coda 与 Coda
    if kind in _CODA_KINDS:
        if not to_codas:
            result.err(
                "MISSING_TO_CODA",
                f"{kind.upper()} 需要一个 To Coda 标记",
                result.mnum(nav_pos),
            )
        else:
            nav.to_coda = to_codas[0]
            if to_codas[0] >= nav_pos:
                result.err(
                    "TO_CODA_AFTER_NAVIGATION",
                    "To Coda 必须位于 D.C./D.S. 指令之前",
                    result.mnum(to_codas[0]),
                )
        if not codas:
            result.err(
                "MISSING_CODA",
                f"{kind.upper()} 需要一个 Coda 目标标记",
                result.mnum(nav_pos),
            )
        else:
            nav.coda = codas[0]
            if codas[0] <= nav_pos:
                result.err(
                    "CODA_BEFORE_NAVIGATION",
                    "Coda 目标必须位于 D.C./D.S. 指令之后",
                    result.mnum(codas[0]),
                )
        if fines:
            result.warn(
                "UNUSED_FINE",
                f"{kind.upper()} 演奏到 Coda 后全曲结束，Fine 标记不会生效",
                result.mnum(fines[0]),
            )
    else:
        if to_codas:
            result.err(
                "TO_CODA_WITHOUT_NAVIGATION",
                f"{kind.upper()} 不是 al Coda 跳转，To Coda 标记无效",
                result.mnum(to_codas[0]),
            )
        if codas:
            result.warn(
                "UNUSED_CODA",
                f"{kind.upper()} 不跳转 Coda，Coda 标记不会被进入",
                result.mnum(codas[0]),
            )

    # D.C./D.S. 指令本身必须在所有反复段之外
    for sec in sections:
        if sec.start <= nav_pos <= sec.close_end:
            result.err(
                "NAVIGATION_INSIDE_REPEAT",
                    "D.C./D.S. 指令必须位于反复段之外（所有 :| 之后）",
                    result.mnum(nav_pos),
                )
            break

    # 永远不会被演奏的小节：导航指令之后、Coda 目标之前
    if nav.coda is not None and nav.coda > nav_pos + 1:
        result.warn(
            "UNREACHABLE_MEASURES",
            f"第 {result.mnum(nav_pos + 1)}–{result.mnum(nav.coda - 1)} 小节位于"
            "跳转指令之后、Coda 之前，永远不会被演奏",
            result.mnum(nav_pos + 1),
        )
