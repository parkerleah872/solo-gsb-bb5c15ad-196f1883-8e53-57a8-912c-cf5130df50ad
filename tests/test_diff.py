"""对齐算法单元测试：最小编辑代价、平局裁决与公共前后缀剥离。

运行：pip install -r requirements.txt httpx && pytest
"""

from __future__ import annotations

import random

import pytest

from app.diff import DiffTooLargeError, align_measures


def ops_of(left, right, max_cells=10**9):
    return align_measures(list(left), list(right), max_cells)


def test_empty_paths():
    assert ops_of([], []) == []
    assert ops_of([1, 2], []) == [("delete", 0, None), ("delete", 1, None)]
    assert ops_of([], [1, 2]) == [("insert", None, 0), ("insert", None, 1)]


def test_identical_paths_are_all_match():
    assert ops_of([1, 2, 3], [1, 2, 3]) == [
        ("match", 0, 0),
        ("match", 1, 1),
        ("match", 2, 2),
    ]


def test_tie_break_prefers_match_then_delete_then_insert():
    # [1,2] vs [2,1]：删除与插入代价相同，按路径顺序先消耗左路径（delete 优先）
    assert ops_of([1, 2], [2, 1]) == [
        ("delete", 0, None),
        ("match", 1, 0),
        ("insert", None, 1),
    ]
    # 镜像：右侧多出的项在匹配之后插入
    assert ops_of([2], [1, 2]) == [("insert", None, 0), ("match", 0, 1)]


def test_common_affixes_are_matched():
    assert ops_of([1, 2, 3, 4], [1, 9, 3, 4]) == [
        ("match", 0, 0),
        ("delete", 1, None),
        ("insert", None, 1),
        ("match", 2, 2),
        ("match", 3, 3),
    ]


def test_repeated_measures_pair_in_order():
    # 同一小节反复出现时按演奏顺序稳定成对：第 3 次为 delete
    assert ops_of([7, 7, 7], [7, 7]) == [
        ("match", 0, 0),
        ("match", 1, 1),
        ("delete", 2, None),
    ]


def test_alignment_size_limit_counts_middle_cells_only():
    # 公共前后缀剥离后中段为 1x1，上限 1 放行
    assert [op for op, _, _ in ops_of([1, 2, 3], [1, 2, 4], max_cells=1)] == [
        "match",
        "match",
        "delete",
        "insert",
    ]
    with pytest.raises(DiffTooLargeError) as exc_info:
        ops_of([1, 2, 3], [4, 5, 6], max_cells=8)  # 3x3 = 9 > 8
    assert exc_info.value.code == "DIFF_TOO_LARGE"


# ---------------------------------------------------------------------------
# 性质测试：与参考实现对比最小编辑距离，并验证结果可重放
# ---------------------------------------------------------------------------


def _reference_distance(a, b):
    """insert/delete 代价均为 1 的最小编辑距离（动态规划参考实现）。"""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + 2)
    return dp[n][m]


def _replay(left, right, ops):
    """按 ops 重放：下标必须连续消耗，match 两侧小节号相等，最终恰好耗尽两侧。"""
    li = ri = 0
    for op, i, j in ops:
        if op == "match":
            assert i == li and j == ri
            assert left[i] == right[j]
            li += 1
            ri += 1
        elif op == "delete":
            assert i == li and j is None
            li += 1
        else:
            assert op == "insert" and j == ri and i is None
            ri += 1
    assert li == len(left) and ri == len(right)


def test_align_matches_reference_distance_and_replays():
    rng = random.Random(20260921)
    for _ in range(1500):
        left = [rng.randint(1, 4) for _ in range(rng.randint(0, 12))]
        right = [rng.randint(1, 4) for _ in range(rng.randint(0, 12))]
        ops = ops_of(left, right)
        # 差异项数 == 最小编辑代价
        assert sum(1 for op, _, _ in ops if op != "match") == _reference_distance(
            left, right
        )
        # 结果可重放且确定性
        _replay(left, right, ops)
        assert ops == ops_of(left, right)
