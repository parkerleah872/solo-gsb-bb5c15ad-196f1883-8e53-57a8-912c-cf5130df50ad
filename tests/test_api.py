"""端到端测试：展开与校验接口。

运行：pip install -r requirements.txt httpx && pytest
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def ms(**kw) -> dict:
    """构造一个小节，measure 自动填充。"""
    return kw


def req(flags: list[dict], **extra) -> dict:
    measures = [{"measure": i + 1, **f} for i, f in enumerate(flags)]
    return {"measures": measures, **extra}


# ---------------------------------------------------------------------------
# 基础展开
# ---------------------------------------------------------------------------


def test_plain_score():
    r = client.post("/api/v1/expand", json=req([ms(), ms(), ms()]))
    assert r.status_code == 200
    body = r.json()
    assert body["sequence"] == [1, 2, 3]
    assert [s["action"] for s in steps_of(body)] == ["continue", "continue", "end"]


def test_simple_paired_repeat():
    data = req(
        [
            ms(repeat_start=True),   # 1 |:
            ms(),                    # 2
            ms(repeat_end=True),     # 3 :|
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 3, 1, 2, 3]
    actions = [s["action"] for s in steps_of(body)]
    assert actions[2] == "repeat_back"
    assert actions[-1] == "end"


def test_repeat_times_three():
    data = req(
        [
            ms(repeat_start=True),
            ms(repeat_end=True, repeat_times=3),
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 1, 2, 1, 2]


def test_implicit_start_repeat():
    # 曲首 :|：1..3 反复后继续
    data = req(
        [
            ms(),  # 1
            ms(),  # 2
            ms(repeat_end=True),  # 3
            ms(),  # 4
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 3, 1, 2, 3, 4]


def steps_of(body):
    return body["steps"]


# ---------------------------------------------------------------------------
# 第一/第二结尾
# ---------------------------------------------------------------------------


def test_first_second_ending():
    data = req(
        [
            ms(repeat_start=True),                              # 1 |:
            ms(),                                               # 2
            ms(ending_start=True, ending_number=1, ending_stop=True,
               repeat_end=True),                                # 3 1. :|
            ms(ending_start=True, ending_number=2, ending_stop=True),  # 4 2.
            ms(),                                               # 5
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 3, 1, 2, 4, 5]
    endings = [s["ending"] for s in steps_of(body)]
    assert endings == [None, None, 1, None, None, 2, None]


def test_implicit_close_final_ending():
    # 最后结尾后省略 :|
    data = req(
        [
            ms(repeat_start=True),                               # 1
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),               # 2 1. :|
            ms(ending_start=True, ending_number=2,
               ending_stop=True),                                # 3 2.
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 1, 3]


def test_three_endings():
    data = req(
        [
            ms(repeat_start=True, repeat_times=3),
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),
            ms(ending_start=True, ending_number=2,
               ending_stop=True, repeat_end=True),
            ms(ending_start=True, ending_number=3, ending_stop=True),
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 1, 3, 1, 4]


def test_nested_repeats():
    data = req(
        [
            ms(repeat_start=True),                      # 1 |: 外层
            ms(repeat_start=True),                      # 2 |: 内层
            ms(),                                       # 3
            ms(repeat_end=True),                        # 4 :|
            ms(repeat_end=True),                        # 5 :| 外层
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    # 内层 2,3 反复一次；外层整体再走一遍时内层不再反复
    assert body["sequence"] == [1, 2, 3, 4, 2, 3, 4, 5, 1, 2, 3, 4, 5]


def test_nested_with_inner_volta():
    data = req(
        [
            ms(repeat_start=True),                       # 1 |:
            ms(repeat_start=True),                       # 2  |:
            ms(),                                        # 3
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),       # 4 1. :|
            ms(ending_start=True, ending_number=2,
               ending_stop=True),                        # 5 2.
            ms(repeat_end=True),                         # 6 :|
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 3, 4, 2, 3, 5, 6, 1, 2, 3, 5, 6]


# ---------------------------------------------------------------------------
# D.C. / D.S.
# ---------------------------------------------------------------------------


def test_dc_al_fine():
    data = req(
        [
            ms(),                          # 1
            ms(fine=True),                 # 2 Fine
            ms(),                          # 3
            ms(),                          # 4
            ms(navigation="dc_al_fine"),   # 5 D.C. al Fine
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 3, 4, 5, 1, 2]
    assert steps_of(body)[-1]["action"] == "fine_end"


def test_ds_al_coda():
    data = req(
        [
            ms(),                             # 1
            ms(segno=True),                   # 2 Segno
            ms(),                             # 3
            ms(to_coda=True),                 # 4 To Coda
            ms(),                             # 5
            ms(navigation="ds_al_coda"),      # 6 D.S. al Coda
            ms(),                             # 7 不可达
            ms(coda=True),                    # 8 Coda
            ms(),                             # 9
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 3, 4, 5, 6, 2, 3, 4, 8, 9]
    actions = [s["action"] for s in steps_of(body)]
    assert "navigation_jump" in actions
    assert "to_coda_jump" in actions
    # 第 7 小节不可达 -> 警告
    assert any(w["code"] == "UNREACHABLE_MEASURES" for w in body["warnings"])


def test_dc_plain_is_al_fine():
    data = req(
        [
            ms(fine=True),
            ms(),
            ms(navigation="dc"),
        ]
    )
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [1, 2, 3, 1]


# ---------------------------------------------------------------------------
# 错误处理
# ---------------------------------------------------------------------------


def test_unclosed_repeat():
    data = req([ms(repeat_start=True), ms()])
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "UNCLOSED_REPEAT"
    assert detail[0]["measure"] == 1


def test_illegal_navigation_missing_segno():
    data = req([ms(), ms(navigation="ds_al_fine")])
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    codes = {d["code"] for d in r.json()["detail"]}
    assert "MISSING_SEGNO" in codes
    assert "MISSING_FINE" in codes


def test_coda_without_targets():
    data = req([ms(), ms(navigation="dc_al_coda")])
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    codes = {d["code"] for d in r.json()["detail"]}
    assert {"MISSING_TO_CODA", "MISSING_CODA"} <= codes


def test_navigation_inside_repeat_rejected():
    data = req(
        [
            ms(repeat_start=True),
            ms(fine=True),
            ms(navigation="dc_al_fine"),
            ms(repeat_end=True),
        ]
    )
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert any(d["code"] == "NAVIGATION_INSIDE_REPEAT" for d in r.json()["detail"])


def test_ending_times_mismatch():
    data = req(
        [
            ms(repeat_start=True, repeat_times=3),
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),
            ms(ending_start=True, ending_number=2, ending_stop=True),
        ]
    )
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert any(d["code"] == "REPEAT_TIMES_ENDING_MISMATCH" for d in r.json()["detail"])


def test_invalid_endings_number_sequence():
    data = req(
        [
            ms(repeat_start=True),
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),
            ms(ending_start=True, ending_number=3, ending_stop=True),
        ]
    )
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert any(d["code"] == "ENDING_NUMBER_SEQUENCE" for d in r.json()["detail"])


def test_error_response_has_no_partial_sequence():
    data = req([ms(repeat_start=True)])
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    body = r.json()
    assert "sequence" not in body
    assert "steps" not in body


def test_measure_numbers_must_increase():
    data = {
        "measures": [
            {"measure": 1},
            {"measure": 1},
        ]
    }
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "MEASURE_NOT_INCREASING"


def test_max_steps_exceeded():
    data = req(
        [
            ms(repeat_start=True, repeat_times=99),
            ms(repeat_end=True),
        ],
        max_steps=10,
    )
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "MAX_STEPS_EXCEEDED"
    assert "measure" in r.json()["detail"][0]


def test_server_cap_rejects_large_max_steps():
    data = req([ms()], max_steps=10_000_000)
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "MAX_STEPS_TOO_LARGE"


def test_validation_endpoint_returns_all_issues():
    data = req(
        [
            ms(repeat_start=True),
            ms(ending_start=True),  # 缺 ending_number
        ]
    )
    r = client.post("/api/v1/validate", json=data)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    codes = {e["code"] for e in body["errors"]}
    assert "UNCLOSED_REPEAT" in codes
    assert "ENDING_NUMBER_MISSING" in codes
    assert body["repeat_sections"]


def test_validation_endpoint_valid():
    data = req([ms(), ms()])
    r = client.post("/api/v1/validate", json=data)
    assert r.status_code == 200
    assert r.json()["valid"] is True


def test_validation_endpoint_warns_unused_fine():
    data = req([ms(fine=True), ms()])
    body = client.post("/api/v1/validate", json=data).json()
    assert body["valid"] is True
    assert any(w["code"] == "UNUSED_FINE" for w in body["warnings"])


def test_multiple_navigation_rejected():
    data = req(
        [
            ms(fine=True),
            ms(navigation="dc_al_fine"),
            ms(navigation="dc_al_fine"),
        ]
    )
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert any(d["code"] == "MULTIPLE_NAVIGATION" for d in r.json()["detail"])


def test_marker_on_repeat_bar_rejected():
    data = req([ms(repeat_start=True, fine=True)])
    r = client.post("/api/v1/expand", json=data)
    assert r.status_code == 422
    assert any(d["code"] == "MARKER_ON_REPEAT_BAR" for d in r.json()["detail"])


def test_repeat_keeps_original_measure_numbers_with_gaps():
    # 原始小节号不连续（例如弱起 / 合并小节）时仍保留原号
    data = {
        "measures": [
            {"measure": 10, "repeat_start": True},
            {"measure": 12, "repeat_end": True},
        ]
    }
    body = client.post("/api/v1/expand", json=data).json()
    assert body["sequence"] == [10, 12, 10, 12]


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 分页展开 /api/v1/expand-page
# ---------------------------------------------------------------------------


def expand_all_pages(data: dict, page_size: int, **extra) -> list[dict]:
    """逐页拉取并拼接全部 steps；校验游标流转的基本不变量。"""
    steps: list[dict] = []
    cursor = None
    for _ in range(1000):  # 防御死循环
        body = {"measures": data["measures"]}
        if cursor is None:
            body["page_size"] = page_size
            body.update(extra)
        else:
            body["cursor"] = cursor
        r = client.post("/api/v1/expand-page", json=body)
        assert r.status_code == 200, r.text
        payload = r.json()
        assert len(payload["steps"]) <= page_size
        steps.extend(payload["steps"])
        if payload["done"]:
            assert payload["next_cursor"] is None
            return steps
        assert payload["next_cursor"]
        cursor = payload["next_cursor"]
    raise AssertionError("分页未在预期步数内结束")


def score_nested_volta_ds_al_coda() -> dict:
    # 嵌套反复 + 内层结尾 + D.S. al Coda + 不可达小节
    return req(
        [
            ms(),                                                # 1
            ms(segno=True),                                      # 2 Segno
            ms(repeat_start=True),                               # 3 |: 外层
            ms(repeat_start=True),                               # 4 |: 内层
            ms(),                                                # 5
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),               # 6 1. :|
            ms(ending_start=True, ending_number=2,
               ending_stop=True),                                # 7 2.
            ms(repeat_end=True),                                 # 8 :| 外层
            ms(to_coda=True),                                    # 9 To Coda
            ms(navigation="ds_al_coda"),                         # 10 D.S. al Coda
            ms(),                                                # 11 不可达
            ms(coda=True),                                       # 12 Coda
            ms(),                                                # 13
        ]
    )


def score_repeat_dc_al_fine() -> dict:
    return req(
        [
            ms(repeat_start=True),          # 1 |:
            ms(),                           # 2
            ms(repeat_end=True),            # 3 :|
            ms(fine=True),                  # 4 Fine
            ms(),                           # 5
            ms(navigation="dc_al_fine"),    # 6 D.C. al Fine
        ]
    )


def score_three_endings() -> dict:
    return req(
        [
            ms(repeat_start=True, repeat_times=3),
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),
            ms(ending_start=True, ending_number=2,
               ending_stop=True, repeat_end=True),
            ms(ending_start=True, ending_number=3, ending_stop=True),
        ]
    )


def score_nested_inner_volta() -> dict:
    return req(
        [
            ms(repeat_start=True),
            ms(repeat_start=True),
            ms(),
            ms(ending_start=True, ending_number=1,
               ending_stop=True, repeat_end=True),
            ms(ending_start=True, ending_number=2, ending_stop=True),
            ms(repeat_end=True),
        ]
    )


@pytest.mark.parametrize(
    "score",
    [
        score_nested_volta_ds_al_coda(),
        score_repeat_dc_al_fine(),
        score_three_endings(),
        score_nested_inner_volta(),
        req([ms(repeat_start=True, repeat_times=4), ms(repeat_end=True)]),
        req([ms(), ms(), ms()]),
    ],
)
@pytest.mark.parametrize("page_size", [1, 2, 3, 5, 7, 100])
def test_expand_page_matches_one_shot(score, page_size):
    """任意页大小下，分页拼接结果与一次性 /expand 逐步一致（含 action/ending/index）。"""
    one_shot = client.post("/api/v1/expand", json=score).json()
    paged = expand_all_pages(score, page_size)
    assert paged == one_shot["steps"]
    assert [s["measure"] for s in paged] == one_shot["sequence"]


def test_expand_page_first_page_requires_page_size():
    r = client.post("/api/v1/expand-page", json=req([ms(), ms()]))
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "PAGE_SIZE_REQUIRED"


def test_expand_page_flow_done_and_cursor():
    data = req([ms(repeat_start=True), ms(repeat_end=True)])  # 共 4 步
    r1 = client.post("/api/v1/expand-page", json={**data, "page_size": 3}).json()
    assert r1["done"] is False
    assert r1["next_cursor"]
    assert [s["index"] for s in r1["steps"]] == [1, 2, 3]
    assert r1["sequence"] == [1, 2, 1]

    r2 = client.post(
        "/api/v1/expand-page", json={"measures": data["measures"], "cursor": r1["next_cursor"]}
    ).json()
    assert r2["done"] is True
    assert r2["next_cursor"] is None
    assert [s["index"] for s in r2["steps"]] == [4]
    assert r2["steps"][0]["action"] == "end"


def test_expand_page_single_page_finishes():
    body = client.post(
        "/api/v1/expand-page", json={**req([ms(), ms()]), "page_size": 10}
    ).json()
    assert body["done"] is True
    assert body["next_cursor"] is None
    assert body["sequence"] == [1, 2]


def test_expand_page_tampered_cursor_rejected():
    data = req([ms(repeat_start=True), ms(), ms(repeat_end=True)])
    first = client.post("/api/v1/expand-page", json={**data, "page_size": 1}).json()
    cursor = first["next_cursor"]
    assert cursor

    payload_part, sig = cursor.split(".")
    tampered = payload_part[:-1] + ("A" if payload_part[-1] != "A" else "B") + "." + sig
    r = client.post("/api/v1/expand-page", json={**data, "cursor": tampered})
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CURSOR_INVALID"

    r = client.post("/api/v1/expand-page", json={**data, "cursor": "not-a-cursor"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CURSOR_INVALID"


def test_expand_page_score_mismatch_rejected():
    data = req([ms(repeat_start=True), ms(), ms(repeat_end=True)])
    first = client.post("/api/v1/expand-page", json={**data, "page_size": 2}).json()
    cursor = first["next_cursor"]

    other = req([ms(), ms(repeat_end=True), ms()])
    r = client.post("/api/v1/expand-page", json={**other, "cursor": cursor})
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CURSOR_SCORE_MISMATCH"


def test_expand_page_param_mismatch_rejected():
    data = req([ms(repeat_start=True), ms(), ms(repeat_end=True)])
    first = client.post("/api/v1/expand-page", json={**data, "page_size": 2}).json()
    cursor = first["next_cursor"]

    r = client.post(
        "/api/v1/expand-page", json={**data, "cursor": cursor, "page_size": 3}
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CURSOR_PARAM_MISMATCH"

    r = client.post(
        "/api/v1/expand-page", json={**data, "cursor": cursor, "max_steps": 9999}
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CURSOR_PARAM_MISMATCH"

    # 显式给出与首页一致的参数则放行
    r = client.post(
        "/api/v1/expand-page", json={**data, "cursor": cursor, "page_size": 2}
    )
    assert r.status_code == 200


def test_expand_page_max_steps_exceeded_across_pages():
    data = req([ms(repeat_start=True, repeat_times=99), ms(repeat_end=True)])
    r = client.post(
        "/api/v1/expand-page", json={**data, "page_size": 5, "max_steps": 10}
    )
    assert r.status_code == 200  # 第一页未超总量
    cursor = r.json()["next_cursor"]
    assert cursor

    r = client.post("/api/v1/expand-page", json={**data, "cursor": cursor})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "MAX_STEPS_EXCEEDED"
    assert "measure" in detail[0]
    assert "steps" not in r.json()


def test_expand_page_size_too_large():
    r = client.post("/api/v1/expand-page", json={**req([ms()]), "page_size": 1001})
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "PAGE_SIZE_TOO_LARGE"


def test_expand_page_structural_error_matches_expand():
    data = req([ms(repeat_start=True), ms()])
    r = client.post("/api/v1/expand-page", json={**data, "page_size": 2})
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "UNCLOSED_REPEAT"

    r = client.post(
        "/api/v1/expand-page",
        json={"measures": [{"measure": 1}, {"measure": 1}], "page_size": 2},
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "MEASURE_NOT_INCREASING"


def test_expand_page_warnings_carried_on_pages():
    body = client.post(
        "/api/v1/expand-page",
        json={**score_nested_volta_ds_al_coda(), "page_size": 4},
    ).json()
    assert any(w["code"] == "UNREACHABLE_MEASURES" for w in body["warnings"])


# ---------------------------------------------------------------------------
# 时间轴 /api/v1/timeline
# ---------------------------------------------------------------------------


def treq(flags: list[dict], **extra) -> dict:
    """构造时间轴请求：每个小节默认 4 拍、首小节默认 120 BPM。"""
    measures = []
    for i, f in enumerate(flags):
        m = {"measure": i + 1, "beats": 4}
        if i == 0:
            m["tempo_bpm"] = 120
        m.update(f)
        measures.append(m)
    return {"measures": measures, **extra}


def test_timeline_plain_score():
    # 3 小节 × 4 拍 @120 BPM：每拍 500ms，每小节 2000ms
    r = client.post("/api/v1/timeline", json=treq([ms(), ms(), ms()]))
    assert r.status_code == 200
    body = r.json()
    assert body["total_duration_ms"] == 6000
    assert [
        (s["occurrence"], s["measure"], s["start_ms"], s["end_ms"], s["tempo_bpm"])
        for s in body["steps"]
    ] == [
        (1, 1, 0, 2000, 120.0),
        (1, 2, 2000, 4000, 120.0),
        (1, 3, 4000, 6000, 120.0),
    ]


def test_timeline_tempo_change_takes_effect_from_marker_measure():
    # 第 2 小节起速度变为 60 BPM：第 2 小节本身即按新速度计时
    body = client.post(
        "/api/v1/timeline",
        json=treq([ms(), ms(tempo_bpm=60), ms()]),
    ).json()
    assert [(s["start_ms"], s["end_ms"], s["tempo_bpm"]) for s in body["steps"]] == [
        (0, 2000, 120.0),
        (2000, 6000, 60.0),
        (6000, 10000, 60.0),
    ]
    assert body["total_duration_ms"] == 10000


def test_timeline_repeat_reapplies_tempo_and_counts_occurrences():
    # |: 1(120) 2(60) :| 反复后再次经过第 1 小节，120 重新生效
    body = client.post(
        "/api/v1/timeline",
        json=treq([ms(repeat_start=True), ms(tempo_bpm=60, repeat_end=True)]),
    ).json()
    assert [(s["occurrence"], s["measure"], s["tempo_bpm"]) for s in body["steps"]] == [
        (1, 1, 120.0),
        (1, 2, 60.0),
        (2, 1, 120.0),
        (2, 2, 60.0),
    ]
    assert [s["start_ms"] for s in body["steps"]] == [0, 2000, 6000, 8000]
    assert body["total_duration_ms"] == 12000


def test_timeline_dc_al_fine_reapplies_tempo():
    # D.C. 折返后再次经过第 1 小节的速度标记，120 重新生效并在 Fine 结束
    body = client.post(
        "/api/v1/timeline",
        json=treq(
            [
                ms(),                       # 1 (120)
                ms(fine=True),              # 2 Fine
                ms(tempo_bpm=60),           # 3 (60)
                ms(navigation="dc_al_fine"),  # 4 D.C. al Fine
            ]
        ),
    ).json()
    # 演奏序列：1,2,3,4,1,2
    assert [(s["occurrence"], s["measure"], s["tempo_bpm"]) for s in body["steps"]] == [
        (1, 1, 120.0),
        (1, 2, 120.0),
        (1, 3, 60.0),
        (1, 4, 60.0),
        (2, 1, 120.0),
        (2, 2, 120.0),
    ]
    # 1,2 @120 各 2000ms；3,4 @60 各 4000ms；折返后 1,2 @120 各 2000ms
    assert body["total_duration_ms"] == 2000 + 2000 + 4000 + 4000 + 2000 + 2000


def test_timeline_matches_expand_sequence():
    # 时间轴步骤的演奏顺序与 /expand 完全一致（含反复与结尾）
    data = req(
        [
            ms(repeat_start=True),                              # 1 |:
            ms(),                                               # 2
            ms(ending_start=True, ending_number=1, ending_stop=True,
               repeat_end=True),                                # 3 1. :|
            ms(ending_start=True, ending_number=2, ending_stop=True),  # 4 2.
            ms(),                                               # 5
        ]
    )
    expanded = client.post("/api/v1/expand", json=data).json()
    tdata = {
        "measures": [
            {"beats": 2, "tempo_bpm": 100, **m} if i == 0 else {"beats": 2, **m}
            for i, m in enumerate(data["measures"])
        ]
    }
    timeline = client.post("/api/v1/timeline", json=tdata).json()
    assert [s["measure"] for s in timeline["steps"]] == expanded["sequence"]
    assert [s["occurrence"] for s in timeline["steps"]] == [1, 1, 1, 2, 2, 1, 1]


def test_timeline_no_stepwise_rounding():
    # 7 小节 × 1 拍 @7 BPM：每小节 60000/7 ≈ 8571.428571ms
    # 逐步取整会得到 7×8571=59997；精确累计后输出取整必须是 60000
    body = client.post(
        "/api/v1/timeline",
        json=treq([ms(beats=1, tempo_bpm=7)] + [ms(beats=1)] * 6),
    ).json()
    assert body["total_duration_ms"] == 60000
    assert [s["end_ms"] for s in body["steps"]] == [
        8571, 17143, 25714, 34286, 42857, 51429, 60000
    ]
    # 相邻步骤共享边界：上一步 end == 下一步 start
    for prev, cur in zip(body["steps"], body["steps"][1:]):
        assert prev["end_ms"] == cur["start_ms"]


def test_timeline_fractional_beats():
    body = client.post(
        "/api/v1/timeline",
        json=treq([ms(beats=3.5), ms(beats=0.5)]),
    ).json()
    # 3.5 拍 @120 = 1750ms；0.5 拍 = 250ms
    assert [(s["start_ms"], s["end_ms"]) for s in body["steps"]] == [(0, 1750), (1750, 2000)]
    assert body["total_duration_ms"] == 2000


def test_timeline_beats_must_be_positive():
    for bad in (0, -2):
        r = client.post("/api/v1/timeline", json=treq([ms(), ms(beats=bad), ms()]))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail[0]["code"] == "INVALID_BEATS"
        assert detail[0]["measure"] == 2
        assert "steps" not in r.json()


def test_timeline_beats_required():
    r = client.post(
        "/api/v1/timeline",
        json={"measures": [{"measure": 1, "beats": 4, "tempo_bpm": 120}, {"measure": 2}]},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "BEATS_MISSING"
    assert detail[0]["measure"] == 2


def test_timeline_tempo_must_be_positive():
    for bad in (0, -120):
        r = client.post("/api/v1/timeline", json=treq([ms(tempo_bpm=bad)]))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail[0]["code"] == "INVALID_TEMPO"
        assert detail[0]["measure"] == 1


def test_timeline_first_measure_must_have_tempo():
    r = client.post("/api/v1/timeline", json=treq([ms(tempo_bpm=None), ms()]))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "TEMPO_MISSING"
    assert detail[0]["measure"] == 1


def test_timeline_later_tempo_marker_does_not_save_missing_initial_tempo():
    r = client.post(
        "/api/v1/timeline",
        json=treq([ms(tempo_bpm=None), ms(tempo_bpm=90)]),
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "TEMPO_MISSING"


def test_timeline_structural_error_has_measure_and_no_partial_timeline():
    r = client.post("/api/v1/timeline", json=treq([ms(repeat_start=True), ms()]))
    assert r.status_code == 422
    body = r.json()
    assert body["detail"][0]["code"] == "UNCLOSED_REPEAT"
    assert body["detail"][0]["measure"] == 1
    assert "steps" not in body
    assert "total_duration_ms" not in body


def test_timeline_max_steps_exceeded():
    r = client.post(
        "/api/v1/timeline",
        json=treq(
            [ms(repeat_start=True, repeat_times=99), ms(repeat_end=True)],
            max_steps=10,
        ),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "MAX_STEPS_EXCEEDED"
    assert "measure" in detail[0]
    assert "steps" not in r.json()


def test_timeline_max_steps_too_large():
    r = client.post("/api/v1/timeline", json=treq([ms()], max_steps=10_000_000))
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "MAX_STEPS_TOO_LARGE"


def test_timeline_measure_numbers_must_increase():
    r = client.post(
        "/api/v1/timeline",
        json={"measures": [{"measure": 1, "beats": 4, "tempo_bpm": 120},
                           {"measure": 1, "beats": 4}]},
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "MEASURE_NOT_INCREASING"


def test_timeline_warnings_carried():
    # 结构警告（如不可达小节）随时间轴响应一并返回
    body = client.post(
        "/api/v1/timeline",
        json=treq(
            [
                ms(),
                ms(to_coda=True),
                ms(navigation="dc_al_coda"),
                ms(),  # 不可达
                ms(coda=True),
            ]
        ),
    ).json()
    assert any(w["code"] == "UNREACHABLE_MEASURES" for w in body["warnings"])


# ---------------------------------------------------------------------------
# 路径对比 /api/v1/path-diff
# ---------------------------------------------------------------------------


def tmeasures(flags: list[dict], tempo=120) -> list[dict]:
    """构造单侧乐谱：每小节默认 4 拍、首小节默认给定 BPM。"""
    measures = []
    for i, f in enumerate(flags):
        m = {"measure": i + 1, "beats": 4}
        if i == 0:
            m["tempo_bpm"] = tempo
        m.update(f)
        measures.append(m)
    return measures


def dreq(
    left_flags: list[dict],
    right_flags: list[dict],
    left_tempo=120,
    right_tempo=120,
    **extra,
) -> dict:
    """构造路径对比请求。"""
    return {
        "left": tmeasures(left_flags, left_tempo),
        "right": tmeasures(right_flags, right_tempo),
        **extra,
    }


def test_path_diff_identical_scores():
    r = client.post("/api/v1/path-diff", json=dreq([ms(), ms()], [ms(), ms()]))
    assert r.status_code == 200
    body = r.json()
    assert [o["op"] for o in body["ops"]] == ["match", "match"]
    assert body["ops"][0] == {
        "op": "match",
        "left_measure": 1,
        "left_occurrence": 1,
        "right_measure": 1,
        "right_occurrence": 1,
    }
    assert body["first_diff_index"] is None
    assert body["total_differences"] == 0
    assert body["left_total_duration_ms"] == body["right_total_duration_ms"] == 4000
    assert body["duration_diff_ms"] == 0


def test_path_diff_left_extra_repeat_yields_deletes_with_occurrence():
    # 左侧反复 3 遍、右侧 2 遍：第三遍的每一步都是 delete，occurrence 为 3
    body = client.post(
        "/api/v1/path-diff",
        json=dreq(
            [ms(repeat_start=True, repeat_times=3), ms(repeat_end=True)],
            [ms(repeat_start=True), ms(repeat_end=True)],
        ),
    ).json()
    assert [(o["op"], o["left_measure"], o["left_occurrence"]) for o in body["ops"]] == [
        ("match", 1, 1),
        ("match", 2, 1),
        ("match", 1, 2),
        ("match", 2, 2),
        ("delete", 1, 3),
        ("delete", 2, 3),
    ]
    assert body["first_diff_index"] == 5
    assert body["total_differences"] == 2
    assert body["duration_diff_ms"] == 4000  # 左侧多奏一遍（2 小节 × 2000ms）


def test_path_diff_right_extra_measures_yields_inserts():
    body = client.post(
        "/api/v1/path-diff",
        json=dreq([ms(), ms()], [ms(), ms(), ms(), ms(beats=2)]),
    ).json()
    ops = body["ops"]
    assert [o["op"] for o in ops] == ["match", "match", "insert", "insert"]
    assert ops[2]["right_measure"] == 3 and ops[2]["right_occurrence"] == 1
    assert ops[2]["left_measure"] is None and ops[2]["left_occurrence"] is None
    assert ops[3]["right_measure"] == 4
    assert body["first_diff_index"] == 3
    assert body["total_differences"] == 2
    # 右侧多 4 拍 + 2 拍 @120 = 3000ms
    assert body["duration_diff_ms"] == -3000


def test_path_diff_tempo_difference_reflected_in_duration_diff():
    # 路径完全相同，仅速度不同：无差异项，但总时长差非零
    body = client.post(
        "/api/v1/path-diff",
        json=dreq([ms(), ms()], [ms(), ms()], left_tempo=120, right_tempo=60),
    ).json()
    assert body["total_differences"] == 0
    assert body["first_diff_index"] is None
    assert body["left_total_duration_ms"] == 4000
    assert body["right_total_duration_ms"] == 8000
    assert body["duration_diff_ms"] == -4000


def test_path_diff_matched_occurrences_pair_in_order():
    # 左路径 1,2,1,3（第一/第二结尾），右路径 1,2：
    # 右侧第 1 遍与左侧第 1 遍配对，左侧第二遍的 1 与第二结尾 3 为 delete
    body = client.post(
        "/api/v1/path-diff",
        json=dreq(
            [
                ms(repeat_start=True),
                ms(ending_start=True, ending_number=1, ending_stop=True,
                   repeat_end=True),
                ms(ending_start=True, ending_number=2, ending_stop=True),
            ],
            [ms(), ms()],
        ),
    ).json()
    assert [
        (o["op"], o["left_measure"], o["left_occurrence"], o["right_occurrence"])
        for o in body["ops"]
    ] == [
        ("match", 1, 1, 1),
        ("match", 2, 1, 1),
        ("delete", 1, 2, None),
        ("delete", 3, 1, None),
    ]
    assert body["first_diff_index"] == 3
    assert body["total_differences"] == 2


def test_path_diff_uses_expansion_semantics_on_both_sides():
    # 左侧 D.C. al Fine、右侧带第一/第二结尾，验证按演奏语义展开后再对齐
    left = tmeasures(
        [
            ms(),                          # 1
            ms(fine=True),                 # 2 Fine
            ms(),                          # 3
            ms(navigation="dc_al_fine"),   # 4 D.C. al Fine
        ]
    )
    right = tmeasures(
        [
            ms(repeat_start=True),                              # 1 |:
            ms(ending_start=True, ending_number=1, ending_stop=True,
               repeat_end=True),                                # 2 1. :|
            ms(ending_start=True, ending_number=2, ending_stop=True),  # 3 2.
        ]
    )
    body = client.post("/api/v1/path-diff", json={"left": left, "right": right}).json()
    # 左路径 1,2,3,4,1,2；右路径 1,2,1,3
    assert [(o["op"], o["left_measure"], o["right_measure"]) for o in body["ops"]] == [
        ("match", 1, 1),
        ("match", 2, 2),
        ("delete", 3, None),
        ("delete", 4, None),
        ("match", 1, 1),
        ("delete", 2, None),
        ("insert", None, 3),
    ]
    assert body["total_differences"] == 4
    assert body["first_diff_index"] == 3


def test_path_diff_left_field_error_has_side_and_measure():
    r = client.post(
        "/api/v1/path-diff",
        json=dreq([ms(), ms(beats=0)], [ms(), ms()]),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "INVALID_BEATS"
    assert detail[0]["measure"] == 2
    assert detail[0]["side"] == "left"
    assert "ops" not in r.json()


def test_path_diff_right_tempo_missing():
    r = client.post(
        "/api/v1/path-diff",
        json={
            "left": [{"measure": 1, "beats": 4, "tempo_bpm": 120}],
            "right": [{"measure": 1, "beats": 4}],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "TEMPO_MISSING"
    assert detail[0]["side"] == "right"


def test_path_diff_both_sides_invalid_reported_together():
    r = client.post(
        "/api/v1/path-diff",
        json={
            "left": [{"measure": 1, "beats": -1, "tempo_bpm": 120}],
            "right": [{"measure": 1, "beats": 4, "tempo_bpm": 120, "repeat_start": True}],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert {(d["side"], d["code"]) for d in detail} == {
        ("left", "INVALID_BEATS"),
        ("right", "UNCLOSED_REPEAT"),
    }
    assert all("measure" in d for d in detail)
    assert "ops" not in r.json()


def test_path_diff_structural_error_has_measure_and_no_partial_result():
    r = client.post(
        "/api/v1/path-diff",
        json=dreq([ms(repeat_start=True), ms()], [ms(), ms()]),
    )
    assert r.status_code == 422
    body = r.json()
    assert body["detail"][0]["code"] == "UNCLOSED_REPEAT"
    assert body["detail"][0]["measure"] == 1
    assert body["detail"][0]["side"] == "left"
    assert "ops" not in body
    assert "total_differences" not in body


def test_path_diff_measure_numbers_must_increase():
    r = client.post(
        "/api/v1/path-diff",
        json={
            "left": [{"measure": 1, "beats": 4, "tempo_bpm": 120}],
            "right": [
                {"measure": 2, "beats": 4, "tempo_bpm": 120},
                {"measure": 2, "beats": 4},
            ],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "MEASURE_NOT_INCREASING"
    assert detail[0]["side"] == "right"


def test_path_diff_max_steps_exceeded_has_side_and_measure():
    r = client.post(
        "/api/v1/path-diff",
        json=dreq(
            [ms(repeat_start=True, repeat_times=99), ms(repeat_end=True)],
            [ms()],
            max_steps=10,
        ),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "MAX_STEPS_EXCEEDED"
    assert detail[0]["side"] == "left"
    assert "measure" in detail[0]
    assert "ops" not in r.json()


def test_path_diff_max_steps_too_large():
    r = client.post("/api/v1/path-diff", json=dreq([ms()], [ms()], max_steps=10_000_000))
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "MAX_STEPS_TOO_LARGE"


def test_path_diff_alignment_size_limit(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "max_diff_cells", 4)
    # 两侧无公共前后缀，中段 3x3 = 9 > 4
    r = client.post(
        "/api/v1/path-diff",
        json={
            "left": [
                {"measure": 1, "beats": 4, "tempo_bpm": 120},
                {"measure": 2, "beats": 4},
                {"measure": 3, "beats": 4},
            ],
            "right": [
                {"measure": 4, "beats": 4, "tempo_bpm": 120},
                {"measure": 5, "beats": 4},
                {"measure": 6, "beats": 4},
            ],
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "DIFF_TOO_LARGE"
    assert "ops" not in r.json()


def test_path_diff_warnings_carry_side():
    body = client.post(
        "/api/v1/path-diff",
        json=dreq(
            [ms(), ms(to_coda=True), ms(navigation="dc_al_coda"), ms(), ms(coda=True)],
            [ms()],
        ),
    ).json()
    assert any(
        w["code"] == "UNREACHABLE_MEASURES" and w["side"] == "left"
        for w in body["warnings"]
    )
    assert all(w["side"] in ("left", "right") for w in body["warnings"])
