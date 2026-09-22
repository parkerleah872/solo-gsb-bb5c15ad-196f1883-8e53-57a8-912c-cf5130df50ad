"""FastAPI 入口：展开、分页展开、结构校验、时间轴与路径对比接口。"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .analyzer import Analysis, analyze
from .config import get_settings
from .diff import DiffTooLargeError, align_measures
from .errors import StructuralError
from .expander import ExpansionError, VMState, expand, run
from .models import (
    ExpandPageRequest,
    ExpandPageResponse,
    ExpandRequest,
    ExpandResponse,
    Issue,
    PathDiffOp,
    PathDiffRequest,
    PathDiffResponse,
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
        "支持一次性展开、无状态分页展开（签名游标续页），"
        "按 beats/tempo 把演奏路径换算为毫秒时间轴，"
        "以及对比两份乐谱的实际演奏路径（最小编辑代价对齐）。"
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


def _check_expandable(measures, max_steps: int):
    """展开前检查：小节号递增、max_steps 上限、结构合法性。

    通过时返回 Analysis；否则返回 422 JSONResponse（不产出任何部分序列）。
    """
    number_errors = _check_measure_numbers(measures)
    if number_errors:
        return _error_response(number_errors)

    if max_steps > settings.max_expansion_steps:
        return _error_response(
            [
                StructuralError(
                    "MAX_STEPS_TOO_LARGE",
                    f"请求的 max_steps={max_steps} 超过服务端上限 "
                    f"{settings.max_expansion_steps}（MAX_EXPANSION_STEPS）",
                )
            ]
        )

    analysis = analyze(measures)
    if analysis.errors:
        return _error_response(analysis.errors)
    return analysis


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
    """path-diff 单侧的校验与展开结果；errors 非空时其余字段无效。"""

    steps: list[TimelineStep] = field(default_factory=list)
    total_ms: int = 0
    warnings: list[StructuralError] = field(default_factory=list)
    errors: list[StructuralError] = field(default_factory=list)


def _side_issue(side: str, err: StructuralError) -> dict:
    return Issue(
        code=err.code, message=err.message, measure=err.measure, side=side
    ).model_dump(exclude_none=True)


def _side_error_response(errors: list[tuple[str, StructuralError]]) -> JSONResponse:
    """path-diff 的 422：每条错误额外标注出错的一侧（left/right）。"""
    return JSONResponse(
        status_code=422,
        content={"detail": [_side_issue(side, e) for side, e in errors]},
    )


def _expand_diff_side(measures: list[TimelineMeasure], max_steps: int) -> _DiffSide:
    """校验并展开 path-diff 的一侧（与 /timeline 完全相同的语义与校验顺序）。

    任一环节失败都只收集错误、不产出任何部分结果；展开期错误同样带小节定位。
    """
    field_errors = check_timeline_fields(measures)
    if field_errors:
        return _DiffSide(errors=field_errors)

    number_errors = _check_measure_numbers(measures)
    if number_errors:
        return _DiffSide(errors=number_errors)

    analysis = analyze(measures)
    if analysis.errors:
        return _DiffSide(errors=analysis.errors)

    try:
        played = expand(analysis, max_steps=max_steps)
    except ExpansionError as exc:
        return _DiffSide(errors=[StructuralError(exc.code, exc.message, exc.measure)])

    steps, total_ms = build_timeline(measures, played)
    return _DiffSide(steps=steps, total_ms=total_ms, warnings=analysis.warnings)


@app.post("/api/v1/path-diff", response_model=PathDiffResponse)
def do_path_diff(req: PathDiffRequest):
    """对比两份乐谱的实际演奏路径。

    两侧分别按与 ``/timeline`` 相同的语义校验并展开（结构、beats/tempo、
    max_steps 任一无效都返回带小节定位与 side 的 422，不产出任何部分结果），
    随后以最小编辑代价（match 0 / insert 1 / delete 1）对齐两条演奏路径；
    相同代价按 match → delete → insert 稳定择优。返回逐项对齐结果、
    首个差异位置、差异总数与两侧总时长差（左 - 右）。
    """
    max_steps = req.max_steps or settings.default_max_steps
    if max_steps > settings.max_expansion_steps:
        return _error_response(
            [
                StructuralError(
                    "MAX_STEPS_TOO_LARGE",
                    f"请求的 max_steps={max_steps} 超过服务端上限 "
                    f"{settings.max_expansion_steps}（MAX_EXPANSION_STEPS）",
                )
            ]
        )

    left = _expand_diff_side(req.left, max_steps)
    right = _expand_diff_side(req.right, max_steps)
    side_errors = [("left", e) for e in left.errors] + [
        ("right", e) for e in right.errors
    ]
    if side_errors:
        return _side_error_response(side_errors)  # type: ignore[return-value]

    try:
        aligned = align_measures(
            [s.measure for s in left.steps],
            [s.measure for s in right.steps],
            settings.max_diff_cells,
        )
    except DiffTooLargeError as exc:
        return _error_response([StructuralError(exc.code, exc.message)])  # type: ignore[return-value]

    ops: list[PathDiffOp] = []
    first_diff_index: int | None = None
    total_differences = 0
    for idx, (op, li, ri) in enumerate(aligned, start=1):
        item = PathDiffOp(op=op)  # type: ignore[arg-type]
        if li is not None:
            item.left_measure = left.steps[li].measure
            item.left_occurrence = left.steps[li].occurrence
        if ri is not None:
            item.right_measure = right.steps[ri].measure
            item.right_occurrence = right.steps[ri].occurrence
        if op != "match":
            total_differences += 1
            if first_diff_index is None:
                first_diff_index = idx
        ops.append(item)

    return PathDiffResponse(
        ops=ops,
        first_diff_index=first_diff_index,
        total_differences=total_differences,
        left_total_duration_ms=left.total_ms,
        right_total_duration_ms=right.total_ms,
        duration_diff_ms=left.total_ms - right.total_ms,
        warnings=[_side_issue("left", w) for w in left.warnings]  # type: ignore[list-item]
        + [_side_issue("right", w) for w in right.warnings],  # type: ignore[list-item]
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
