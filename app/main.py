"""FastAPI 入口：展开、分页展开、结构校验、时间轴与路径对比接口。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .analyzer import Analysis, analyze
from .config import get_settings
from .diff import align_paths
from .errors import StructuralError
from .expander import ExpansionError, VMState, expand, run
from .models import (
    ExpandPageRequest,
    ExpandPageResponse,
    ExpandRequest,
    ExpandResponse,
    Issue,
    PathDiffItem,
    PathDiffRequest,
    PathDiffResponse,
    PathDiffSideRef,
    Step,
    TimelineMeasure,
    TimelineRequest,
    TimelineResponse,
    TimelineStep,
    ValidateResponse,
)
from .pagination import (
    CursorError,
    decode_cursor,
    encode_cursor,
    measures_fingerprint,
)
from .timeline import build_timeline, check_timeline_fields

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "提交按小节排列的结构标记（成对反复、第一/第二结尾、"
        "D.C./D.S. al Coda/Fine），返回按演奏语义展开的实际小节序列。"
        "支持一次性展开、无状态分页展开（签名游标续页）、"
        "按 beats/tempo 把演奏路径换算为毫秒时间轴，"
        "以及对比两份乐谱的实际演奏路径（path-diff）。"
    ),
)


def _issue(err: StructuralError) -> Issue:
    return Issue(code=err.code, message=err.message, measure=err.measure)


def _error_response(errors: list[StructuralError]) -> JSONResponse:
    """统一的 422 错误体：detail 为带错误码与小节定位的列表。"""
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                Issue(code=e.code, message=e.message, measure=e.measure).model_dump(
                    exclude_none=True
                )
                for e in errors
            ]
        },
    )


def _expand_precheck(measures, max_steps: int):
    """展开前检查：小节号递增、max_steps 上限、结构合法性。

    通过时返回 Analysis；否则返回 StructuralError 列表（不产出任何部分序列）。
    """
    number_errors = _check_measure_numbers(measures)
    if number_errors:
        return number_errors

    if max_steps > settings.max_expansion_steps:
        return [
            StructuralError(
                "MAX_STEPS_TOO_LARGE",
                f"请求的 max_steps={max_steps} 超过服务端上限 "
                f"{settings.max_expansion_steps}（MAX_EXPANSION_STEPS）",
            )
        ]

    analysis = analyze(measures)
    if analysis.errors:
        return analysis.errors
    return analysis


def _check_expandable(measures, max_steps: int):
    """展开前检查的 422 包装：通过时返回 Analysis，否则返回 JSONResponse。"""
    checked = _expand_precheck(measures, max_steps)
    if isinstance(checked, list):
        return _error_response(checked)
    return checked


def _check_measure_numbers(measures) -> list[StructuralError]:
    """小节号必须严格递增（Pydantic 只保证正整数，不保证顺序）。"""
    errors: list[StructuralError] = []
    for prev, cur in zip(measures, measures[1:]):
        if cur.measure <= prev.measure:
            errors.append(
                StructuralError(
                    "MEASURE_NOT_INCREASING",
                    f"小节号必须严格递增：第 {cur.measure} 小节出现在第 {prev.measure} 小节之后",
                    cur.measure,
                )
            )
    return errors


def _sections_payload(analysis) -> list[dict]:
    out = []
    for sec in analysis.sections:
        out.append(
            {
                "start_measure": analysis.mnum(sec.start),
                "end_measure": analysis.mnum(sec.close_end),
                "times": sec.times,
                "endings": [
                    {
                        "number": g.number,
                        "start_measure": analysis.mnum(g.start),
                        "end_measure": analysis.mnum(g.end),
                    }
                    for g in sorted(sec.endings.values(), key=lambda g: g.number)
                ],
                "implicit_start": sec.start_marker is None,
                "implicit_close": sec.implicit_close,
            }
        )
    return out


@app.exception_handler(ExpansionError)
async def expansion_error_handler(request: Request, exc: ExpansionError) -> JSONResponse:
    # 展开期错误同样带小节定位，且不返回任何部分序列
    issue = Issue(code=exc.code, message=exc.message, measure=exc.measure)
    return JSONResponse(status_code=422, content={"detail": [issue.model_dump(exclude_none=True)]})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/validate", response_model=ValidateResponse)
def validate(req: ExpandRequest) -> ValidateResponse:
    """只做结构校验，不展开；无论是否合法都返回 200 与全部问题。"""
    number_errors = _check_measure_numbers(req.measures)
    analysis = analyze(req.measures)
    errors = number_errors + analysis.errors
    return ValidateResponse(
        valid=not errors,
        errors=[_issue(e).model_dump(exclude_none=True) for e in errors],  # type: ignore[list-item]
        warnings=[_issue(w).model_dump(exclude_none=True) for w in analysis.warnings],  # type: ignore[list-item]
        repeat_sections=_sections_payload(analysis),
    )


@app.post("/api/v1/expand", response_model=ExpandResponse)
def do_expand(req: ExpandRequest):
    """按演奏语义展开；结构非法或超步数时返回 422，不产出部分序列。"""
    max_steps = req.max_steps or settings.default_max_steps
    checked = _check_expandable(req.measures, max_steps)
    if isinstance(checked, JSONResponse):
        return checked  # type: ignore[return-value]
    analysis: Analysis = checked

    played = expand(analysis, max_steps=max_steps)
    return ExpandResponse(
        sequence=[s.measure for s in played],
        steps=[
            Step(index=i + 1, measure=s.measure, ending=s.ending, action=s.action)
            for i, s in enumerate(played)
        ],
        total_steps=len(played),
        warnings=[_issue(w).model_dump(exclude_none=True) for w in analysis.warnings],  # type: ignore[list-item]
    )


@app.post("/api/v1/timeline", response_model=TimelineResponse)
def do_timeline(req: TimelineRequest):
    """把实际演奏路径换算为毫秒时间轴。

    复用与 ``/expand`` 相同的结构校验与展开语义；每个小节须给出正数
    ``beats``，首小节必须给出 ``tempo_bpm`` 作为初始速度，反复或
    D.C./D.S. 再次经过速度标记时重新生效。非法节拍或速度、结构非法、
    展开超限都返回带小节定位的 422，且不产出任何部分时间轴。
    """
    field_errors = check_timeline_fields(req.measures)
    if field_errors:
        return _error_response(field_errors)  # type: ignore[return-value]

    max_steps = req.max_steps or settings.default_max_steps
    checked = _check_expandable(req.measures, max_steps)
    if isinstance(checked, JSONResponse):
        return checked  # type: ignore[return-value]
    analysis: Analysis = checked

    played = expand(analysis, max_steps=max_steps)
    steps, total_ms = build_timeline(req.measures, played)
    return TimelineResponse(
        steps=steps,
        total_duration_ms=total_ms,
        warnings=[_issue(w).model_dump(exclude_none=True) for w in analysis.warnings],  # type: ignore[list-item]
    )


# ---------------------------------------------------------------------------
# 路径对比 /api/v1/path-diff
# ---------------------------------------------------------------------------


@dataclass
class _DiffSide:
    """path-diff 单侧的处理结果：全部校验通过时携带时间轴，否则携带错误。"""

    errors: list[StructuralError]
    warnings: list[StructuralError]
    steps: list[TimelineStep]
    total_ms: int


def _prepare_diff_side(measures: list[TimelineMeasure], max_steps: int) -> _DiffSide:
    """校验并展开 path-diff 的单侧乐谱。

    校验顺序与展开、计时语义和 ``/timeline`` 完全一致；任何错误都完整
    收集后返回，由调用方统一转成 422（不产出任何部分对比结果）。
    """
    empty = _DiffSide(errors=[], warnings=[], steps=[], total_ms=0)

    field_errors = check_timeline_fields(measures)
    if field_errors:
        empty.errors = field_errors
        return empty

    checked = _expand_precheck(measures, max_steps)
    if isinstance(checked, list):
        empty.errors = checked
        return empty
    analysis: Analysis = checked

    try:
        played = expand(analysis, max_steps=max_steps)
    except ExpansionError as exc:
        empty.errors = [StructuralError(exc.code, exc.message, exc.measure)]
        return empty

    steps, total_ms = build_timeline(measures, played)
    return _DiffSide(
        errors=[], warnings=analysis.warnings, steps=steps, total_ms=total_ms
    )


def _side_issue(side: str, err: StructuralError) -> dict:
    """带 side（left/right）标记的错误/警告条目。"""
    return Issue(
        code=err.code, message=err.message, measure=err.measure, side=side  # type: ignore[arg-type]
    ).model_dump(exclude_none=True)


def _side_error_response(left_errors, right_errors) -> JSONResponse:
    """path-diff 的 422：每条错误带 side 与小节定位，不产出任何部分结果。"""
    detail = [_side_issue("left", e) for e in left_errors] + [
        _side_issue("right", e) for e in right_errors
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.post("/api/v1/path-diff", response_model=PathDiffResponse)
def do_path_diff(req: PathDiffRequest):
    """对比两份乐谱的实际演奏路径。

    left/right 分别按与 ``/timeline`` 相同的结构校验、展开与计时语义处理，
    再用最小编辑代价（insert + delete，即 LCS 距离）对齐两条实际演奏路径；
    相同代价时按路径顺序稳定择优（先 match、再 delete、后 insert）。
    任一侧的结构、节拍速度或展开上限非法时，返回带 side 与小节定位的 422，
    不产出任何部分对比结果。
    """
    max_steps = req.max_steps or settings.default_max_steps

    left = _prepare_diff_side(req.left.measures, max_steps)
    right = _prepare_diff_side(req.right.measures, max_steps)
    if left.errors or right.errors:
        return _side_error_response(left.errors, right.errors)  # type: ignore[return-value]

    cells = len(left.steps) * len(right.steps)
    if cells > settings.max_diff_cells:
        return _error_response(  # type: ignore[return-value]
            [
                StructuralError(
                    "DIFF_TOO_LARGE",
                    f"两侧展开路径长度乘积 {len(left.steps)}×{len(right.steps)}={cells} "
                    f"超过服务端上限 {settings.max_diff_cells}（MAX_DIFF_CELLS）",
                )
            ]
        )

    ops = align_paths(
        [s.measure for s in left.steps], [s.measure for s in right.steps]
    )

    def _ref(side_steps: list[TimelineStep], index: int | None) -> PathDiffSideRef | None:
        if index is None:
            return None
        s = side_steps[index]
        return PathDiffSideRef(index=index + 1, measure=s.measure, occurrence=s.occurrence)

    items: list[PathDiffItem] = []
    matches = 0
    diff_count = 0
    first_diff_index: int | None = None
    for op in ops:
        if op.kind == "match":
            matches += 1
        else:
            diff_count += 1
            if first_diff_index is None:
                first_diff_index = len(items) + 1
        items.append(
            PathDiffItem(
                type=op.kind,
                left=_ref(left.steps, op.left_index),
                right=_ref(right.steps, op.right_index),
            )
        )

    return PathDiffResponse(
        items=items,
        left_steps=len(left.steps),
        right_steps=len(right.steps),
        matches=matches,
        diff_count=diff_count,
        first_diff_index=first_diff_index,
        left_total_duration_ms=left.total_ms,
        right_total_duration_ms=right.total_ms,
        total_duration_diff_ms=right.total_ms - left.total_ms,
        warnings=[_side_issue("left", w) for w in left.warnings]  # type: ignore[list-item]
        + [_side_issue("right", w) for w in right.warnings],
    )


@app.post("/api/v1/expand-page", response_model=ExpandPageResponse)
def do_expand_page(req: ExpandPageRequest):
    """无状态分页展开。

    首页携带 ``measures`` + ``page_size``；续页携带原 ``measures`` + 上一页
    返回的 ``next_cursor``。游标由服务端密钥签名并绑定乐谱指纹与分页参数，
    续页直接从游标中的虚拟机断点继续（不重放），全部页拼接后与一次性
    ``/expand`` 的步骤及 action 完全一致。
    """
    fingerprint = measures_fingerprint(req.measures)

    if req.cursor is None:
        # ---- 首页 ----
        if req.page_size is None:
            return _error_response(
                [
                    StructuralError(
                        "PAGE_SIZE_REQUIRED",
                        "首次分页请求必须提供 page_size",
                    )
                ]
            )
        page_size = req.page_size
        max_steps = req.max_steps or settings.default_max_steps
        state = VMState()
    else:
        # ---- 续页：验签并核对游标绑定的乐谱与参数 ----
        try:
            payload = decode_cursor(settings.cursor_secret, req.cursor)
        except CursorError as exc:
            return _error_response([StructuralError(exc.code, exc.message)])

        if req.page_size is not None and req.page_size != payload["page_size"]:
            return _error_response(
                [
                    StructuralError(
                        "CURSOR_PARAM_MISMATCH",
                        f"page_size={req.page_size} 与游标绑定的 "
                        f"page_size={payload['page_size']} 不一致",
                    )
                ]
            )
        if req.max_steps is not None and req.max_steps != payload["max_steps"]:
            return _error_response(
                [
                    StructuralError(
                        "CURSOR_PARAM_MISMATCH",
                        f"max_steps={req.max_steps} 与游标绑定的 "
                        f"max_steps={payload['max_steps']} 不一致",
                    )
                ]
            )
        if fingerprint != payload["fp"]:
            return _error_response(
                [
                    StructuralError(
                        "CURSOR_SCORE_MISMATCH",
                        "提交的 measures 与游标绑定的乐谱不一致，请使用原始乐谱续页",
                    )
                ]
            )

        page_size = payload["page_size"]
        max_steps = payload["max_steps"]
        state = payload["state"]

    if page_size > settings.max_page_size:
        return _error_response(
            [
                StructuralError(
                    "PAGE_SIZE_TOO_LARGE",
                    f"page_size={page_size} 超过服务端上限 "
                    f"{settings.max_page_size}（MAX_PAGE_SIZE）",
                )
            ]
        )

    checked = _check_expandable(req.measures, max_steps)
    if isinstance(checked, JSONResponse):
        return checked  # type: ignore[return-value]
    analysis = checked

    # 本页预算：页大小与剩余总步数上限取小；从断点直接续跑，不做从头重放
    base = state.emitted
    budget = min(page_size, max_steps - base)
    page = run(analysis, state, budget)

    if not state.done and state.emitted >= max_steps:
        # 与 /expand 一致：总步数超限时报错，不产出部分序列
        raise ExpansionError(
            "MAX_STEPS_EXCEEDED",
            f"展开超过最大步数 {max_steps}，请检查是否存在异常反复或循环",
            analysis.mnum(state.pos),
        )

    next_cursor = None
    if not state.done:
        next_cursor = encode_cursor(
            settings.cursor_secret,
            fingerprint=fingerprint,
            page_size=page_size,
            max_steps=max_steps,
            state=state,
        )

    return ExpandPageResponse(
        sequence=[s.measure for s in page],
        steps=[
            Step(index=base + i + 1, measure=s.measure, ending=s.ending, action=s.action)
            for i, s in enumerate(page)
        ],
        done=state.done,
        next_cursor=next_cursor,
        warnings=[_issue(w).model_dump(exclude_none=True) for w in analysis.warnings],  # type: ignore[list-item]
    )
