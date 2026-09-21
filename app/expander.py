"""展开虚拟机：按演奏语义生成实际小节序列。

状态模型
--------
* 每个反复段在每一个“演奏纪元”（epoch）中维护一个当前遍数计数器：
  epoch 0 = 从头至 D.C./D.S. 指令的首遍演奏；epoch 1 = 折返回去后的第二遍。
* 首遍中，计数器在首次进入反复段时置 1；段内的 :| 使遍数 +1 并跳回段首，
  到达声明遍数后继续向后。嵌套反复在外层第二遍中已经“完成”，
  因而内层不再反复（与常见制谱软件的展开约定一致）。
* 结尾（房子）小节只在“结尾号 == 所属反复段当前遍数”时演奏，其余跳过。
* 按标准演奏惯例，D.C./D.S. 折返后不再重复此前已奏过的反复段：
  新纪元中首次触及反复段时直接视为最后一遍（:| 不再跳回，只走最后结尾）。
* Fine / To Coda 只在折返后的第二遍生效；D.C./D.S. 指令只触发一次。

分页
----
``run`` 以 ``VMState`` 为断点：当前小节位置、演奏纪元、各反复段在各纪元的
遍数计数器、已产出步数。该状态可整体序列化进分页游标，续页时直接恢复继续
执行，无需从头重放，因此跨页结果与一次性展开逐步一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .analyzer import Analysis, Navigation, Section


class ExpansionError(Exception):
    """运行期错误（非法跳转 / 超过最大步数）。"""

    def __init__(self, code: str, message: str, measure: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.measure = measure


@dataclass
class PlayedStep:
    measure: int
    ending: int | None
    action: str


@dataclass
class VMState:
    """展开虚拟机的完整状态（分页断点）。

    * ``pos``     下一个小节位置（0 基）；
    * ``epoch``   演奏纪元：0 = 首遍，1 = D.C./D.S. 折返之后；
    * ``passes``  (反复段 sid, epoch) -> 当前遍数（嵌套反复各自独立计数）；
    * ``emitted`` 已产出的步数（跨页连续编号与 max_steps 总量控制）；
    * ``done``    整曲是否已展开完毕（为 True 时不再签发游标）。
    """

    pos: int = 0
    epoch: int = 0
    passes: dict[tuple[int, int], int] = field(default_factory=dict)
    emitted: int = 0
    done: bool = False

    def to_json(self) -> dict:
        return {
            "pos": self.pos,
            "epoch": self.epoch,
            "emitted": self.emitted,
            "passes": [[sid, ep, cnt] for (sid, ep), cnt in sorted(self.passes.items())],
        }

    @classmethod
    def from_json(cls, data: object) -> "VMState":
        """从游标负载恢复状态；结构非法时抛 ValueError（由调用方映射为 422）。"""
        if not isinstance(data, dict):
            raise ValueError("state 必须是 JSON 对象")

        def _nonneg_int(key: str) -> int:
            v = data.get(key)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(f"state.{key} 必须是非负整数")
            return v

        pos = _nonneg_int("pos")
        epoch = _nonneg_int("epoch")
        emitted = _nonneg_int("emitted")

        raw_passes = data.get("passes")
        if not isinstance(raw_passes, list):
            raise ValueError("state.passes 必须是数组")
        passes: dict[tuple[int, int], int] = {}
        for item in raw_passes:
            if (
                not isinstance(item, list)
                or len(item) != 3
                or any(not isinstance(x, int) or isinstance(x, bool) for x in item)
                or item[0] < 0
                or item[1] < 0
                or item[2] < 1
            ):
                raise ValueError("state.passes 元素必须是 [sid, epoch, 遍数(≥1)]")
            passes[(item[0], item[1])] = item[2]

        return cls(pos=pos, epoch=epoch, passes=passes, emitted=emitted, done=False)


def run(analysis: Analysis, state: VMState, budget: int) -> list[PlayedStep]:
    """从 ``state`` 继续展开，最多产出 ``budget`` 步。

    返回本批步骤并就地推进 ``state``；整曲展开完毕时 ``state.done`` 置 True。
    分析阶段存在错误时不应调用本函数。
    """
    n = len(analysis.measures)
    measures = analysis.measures

    # 结尾小节 -> 所属反复段
    ending_owner: dict[int, Section] = {}
    for sec in analysis.sections:
        for g in sec.endings.values():
            for p in range(g.start, g.end + 1):
                ending_owner[p] = sec

    # (sid, epoch) -> 当前遍数（直接挂载在 VMState 上，跨页保留）
    pass_state = state.passes

    def current_pass(sec: Section, epoch: int) -> int:
        key = (sec.sid, epoch)
        if key not in pass_state:
            # 折返后的纪元：已奏过的反复段不再重复，直接视为最后一遍
            pass_state[key] = sec.times if epoch > 0 else 1
        return pass_state[key]

    def sections_at(pos: int) -> list[Section]:
        return [s for s in analysis.sections if s.start <= pos <= s.close_end]

    steps: list[PlayedStep] = []

    epoch = state.epoch
    pos = state.pos
    finished = False

    def add_step(ending: int | None, action: str) -> None:
        steps.append(PlayedStep(measure=measures[pos].measure, ending=ending, action=action))

    while pos < n:
        if len(steps) >= budget:
            # 本批预算耗尽：保存断点，等待续页（一次性展开时由外层转为报错）
            break

        m = measures[pos]
        nav: Navigation | None = analysis.navigation

        # ---- 结尾（房子）过滤：只演奏与当前遍数匹配的结尾 ----
        ending_no: int | None = None
        if pos in ending_owner:
            owner = ending_owner[pos]
            ending_no = analysis.ending_at[pos]
            if ending_no != current_pass(owner, epoch):
                pos += 1
                continue

        # 首次进入包含本小节的反复段（含 D.S./D.C. 从段中进入的情况）
        for sec in sections_at(pos):
            current_pass(sec, epoch)

        action = "continue"
        next_pos = pos + 1
        terminate = False

        # ---- 右反复记号 :| ----
        if m.repeat_end:
            sec = analysis.end_owner.get(pos)
            if sec is None:  # 理论上不会发生：分析阶段已报错
                raise ExpansionError(
                    "INVALID_REPEAT_END",
                    "右反复记号 :| 找不到所属反复段",
                    m.measure,
                )
            passed = current_pass(sec, epoch)
            if passed < sec.times:
                pass_state[(sec.sid, epoch)] = passed + 1
                action = "repeat_back"
                next_pos = sec.start
            else:
                action = "repeat_finish"

        # ---- Fine：仅折返后生效 ----
        elif m.fine and epoch > 0 and nav and nav.fine == pos:
            action = "fine_end"
            terminate = True

        # ---- To Coda：仅折返后生效 ----
        elif m.to_coda and epoch > 0 and nav and nav.to_coda == pos:
            if nav.coda is None:
                raise ExpansionError(
                    "INVALID_CODA_JUMP",
                    "To Coda 找不到 Coda 目标",
                    m.measure,
                )
            action = "to_coda_jump"
            next_pos = nav.coda

        # ---- D.C. / D.S.：只在首遍触发一次 ----
        elif nav is not None and nav.position == pos and epoch == 0:
            epoch = 1
            if nav.kind.startswith("ds"):
                if nav.segno is None:
                    raise ExpansionError(
                        "INVALID_NAVIGATION_TARGET",
                        "D.S. 缺少 Segno 目标",
                        m.measure,
                    )
                next_pos = nav.segno
            else:
                next_pos = 0
            action = "navigation_jump"

        add_step(ending_no if ending_owner.get(pos) else None, action)
        pos = next_pos

        if terminate:
            finished = True
            break

        # 防御：折返后若仍走到导航指令处（没有 Fine/To Coda 兜底），直接结束
        if (
            epoch > 0
            and nav is not None
            and pos == nav.position
            and nav.fine is None
            and nav.to_coda is None
        ):
            steps[-1] = PlayedStep(
                measure=steps[-1].measure, ending=steps[-1].ending, action="end"
            )
            finished = True
            break

    if pos >= n:
        finished = True

    if finished:
        # 自然走到曲尾时，把最后一步标为 end；Fine 终止（fine_end）等语义动作保留。
        # 终止一定发生在刚产出本批最后一步之后，因此最后一步必在本批中。
        if steps and steps[-1].action in ("continue", "repeat_finish"):
            steps[-1] = PlayedStep(
                measure=steps[-1].measure, ending=steps[-1].ending, action="end"
            )
        state.done = True

    state.pos = pos
    state.epoch = epoch
    state.emitted += len(steps)
    return steps


def expand(analysis: Analysis, max_steps: int) -> list[PlayedStep]:
    """一次性执行展开。分析阶段存在错误时不应调用本函数。"""
    state = VMState()
    steps = run(analysis, state, max_steps)
    if not state.done:
        raise ExpansionError(
            "MAX_STEPS_EXCEEDED",
            f"展开超过最大步数 {max_steps}，请检查是否存在异常反复或循环",
            analysis.mnum(state.pos),
        )
    return steps
