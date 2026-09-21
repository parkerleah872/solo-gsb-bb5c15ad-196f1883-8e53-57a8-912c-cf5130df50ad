"""请求 / 响应的 Pydantic 模型。

标记约定（每个标记都是 Measure 上的布尔字段，同小节可组合多个语义标记）：

反复记号
- ``repeat_start``        左反复记号 |:
- ``repeat_end``          右反复记号 :|
- ``repeat_times``        该反复段的演奏遍数，写在 |: 或 :| 小节上（默认 2）

第一/第二（三、四……）结尾
- ``ending_start``        该小节是一个结尾（房子）的起始小节
- ``ending_number``       结尾序号（1 = 第一结尾，2 = 第二结尾……）
- ``ending_stop``         结尾段结束小节；省略时默认以下一个结尾起始或后续普通小节为界

导航记号
- ``segno``               𝄋 Segno
- ``coda``                𝄌 Coda 目标
- ``fine``                Fine（到此结束）
- ``to_coda``             To Coda（从此处跳往 Coda）
- ``navigation``          "dc" | "ds" | "dc_al_coda" | "ds_al_coda" | "dc_al_fine" | "ds_al_fine"
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

NavigationType = Literal[
    "dc",
    "ds",
    "dc_al_coda",
    "ds_al_coda",
    "dc_al_fine",
    "ds_al_fine",
]


class Measure(BaseModel):
    """按小节排列的一个结构标记单元。"""

    model_config = ConfigDict(extra="forbid")

    measure: PositiveInt = Field(..., description="原始小节号，须为正整数且严格递增")

    repeat_start: bool = Field(default=False, description="左反复记号 |:")
    repeat_end: bool = Field(default=False, description="右反复记号 :|")
    repeat_times: Optional[PositiveInt] = Field(
        default=None, ge=2, description="反复段演奏遍数（≥2），写在 |: 或 :| 小节"
    )

    ending_start: bool = Field(default=False, description="结尾（房子）起始小节")
    ending_number: Optional[PositiveInt] = Field(
        default=None, description="结尾序号：1=第一结尾，2=第二结尾……"
    )
    ending_stop: bool = Field(default=False, description="结尾段结束小节")

    segno: bool = Field(default=False, description="Segno 𝄋 标记")
    coda: bool = Field(default=False, description="Coda 𝄌 目标")
    fine: bool = Field(default=False, description="Fine 标记")
    to_coda: bool = Field(default=False, description="To Coda 标记")
    navigation: Optional[NavigationType] = Field(
        default=None, description="D.C./D.S. 跳转指令"
    )


class ExpandRequest(BaseModel):
    """展开接口请求体。"""

    model_config = ConfigDict(extra="forbid")

    measures: list[Measure] = Field(..., min_length=1, description="按小节排列的结构标记")
    max_steps: Optional[PositiveInt] = Field(
        default=10000,
        description="允许的最大展开步数（默认 10000，服务端硬上限由 MAX_EXPANSION_STEPS 配置）",
    )


class TimelineMeasure(Measure):
    """时间轴接口的小节：在结构标记之外携带拍数与可选速度标记。

    ``beats`` / ``tempo_bpm`` 的取值合法性（正数、有限、首小节必须有速度）
    由服务端统一校验并返回带小节定位的 422，因此这里不加 Pydantic 约束。
    """

    beats: Optional[float] = Field(
        default=None, description="该小节的拍数（正数，必填）"
    )
    tempo_bpm: Optional[float] = Field(
        default=None,
        description="速度标记（拍/分钟，正数）；首小节必填，反复或 D.C./D.S. 再次经过时重新生效",
    )


class TimelineRequest(BaseModel):
    """时间轴接口请求体。"""

    model_config = ConfigDict(extra="forbid")

    measures: list[TimelineMeasure] = Field(
        ..., min_length=1, description="按小节排列的结构标记 + 每小节 beats / 可选 tempo_bpm"
    )
    max_steps: Optional[PositiveInt] = Field(
        default=10000,
        description="允许的最大展开步数（默认 10000，服务端硬上限由 MAX_EXPANSION_STEPS 配置）",
    )


class Step(BaseModel):
    """展开结果中的一个演奏步骤。"""

    index: int = Field(..., description="从 1 开始的演奏顺序")
    measure: int = Field(..., description="原始小节号")
    ending: Optional[int] = Field(default=None, description="该步命中的结尾序号（若在结尾内）")
    action: str = Field(..., description="演奏完该小节后发生的语义动作（continue/repeat_back/...）")


class Issue(BaseModel):
    code: str
    message: str
    measure: Optional[int] = None


class TimelineStep(BaseModel):
    """时间轴上的一个演奏步骤。"""

    occurrence: int = Field(..., description="该小节第几次被演奏（从 1 开始）")
    measure: int = Field(..., description="原始小节号")
    start_ms: int = Field(..., description="该步开始时刻（毫秒，输出时四舍五入）")
    end_ms: int = Field(..., description="该步结束时刻（毫秒，输出时四舍五入）")
    tempo_bpm: float = Field(..., description="该步生效的速度（拍/分钟）")


class TimelineResponse(BaseModel):
    steps: list[TimelineStep] = Field(..., description="按演奏顺序排列的时间轴步骤")
    total_duration_ms: int = Field(..., description="全曲总时长（毫秒，输出时四舍五入）")
    warnings: list[Issue] = Field(default_factory=list)


class ExpandPageRequest(BaseModel):
    """分页展开请求体。

    首页：携带 ``measures`` 与 ``page_size``（``cursor`` 省略）；
    续页：携带与首页完全一致的 ``measures`` 和上一页响应的 ``next_cursor``。
    """

    model_config = ConfigDict(extra="forbid")

    measures: list[Measure] = Field(..., min_length=1, description="按小节排列的结构标记（续页时须与首页一致）")
    page_size: Optional[PositiveInt] = Field(
        default=None,
        description="每页步数；首页必填，续页省略或与首页一致（服务端硬上限由 MAX_PAGE_SIZE 配置）",
    )
    cursor: Optional[str] = Field(
        default=None, description="上一页响应的 next_cursor；省略表示首页"
    )
    max_steps: Optional[PositiveInt] = Field(
        default=None,
        description="允许的最大展开总步数（默认取服务端 DEFAULT_MAX_STEPS）；续页省略或与首页一致",
    )


class ExpandPageResponse(BaseModel):
    sequence: list[int] = Field(..., description="本页实际演奏的原始小节号序列")
    steps: list[Step] = Field(..., description="本页步骤；index 为跨页连续编号")
    done: bool = Field(..., description="整曲是否已展开完毕")
    next_cursor: Optional[str] = Field(
        default=None, description="续页游标（签名令牌）；done=True 时为 null"
    )
    warnings: list[Issue] = Field(default_factory=list)


class ExpandResponse(BaseModel):
    sequence: list[int] = Field(..., description="实际演奏的原始小节号序列")
    steps: list[Step]
    total_steps: int
    warnings: list[Issue] = Field(default_factory=list)


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[Issue]
    warnings: list[Issue]
    repeat_sections: list[dict] = Field(
        default_factory=list, description="解析出的反复段（便于人工核对结构）"
    )


class ErrorResponse(BaseModel):
    """422 错误响应：错误带小节定位，且不产出部分序列。"""

    detail: list[Issue]
